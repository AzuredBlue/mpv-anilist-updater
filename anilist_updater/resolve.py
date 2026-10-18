"""Find the AniList entry for a parsed file, including absolute episode numbers across seasons."""

import logging
import re
from collections import UserDict
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from .api import AniListClient, media_title
from .errors import AniListError, SkipUpdate
from .models import AnimeInfo, FileInfo, Status
from .release import fold_title

log = logging.getLogger(__name__)

Media = dict[str, Any]

TIMES_SIGN = chr(0xD7)  # AniList writes "Durarara!!" sequels with it, which a search for "X2" doesn't find
_TIMES = re.compile(r"\bx(?=\d\b)", re.IGNORECASE)
_HONORIFIC = re.compile(r"\b(\w{3,}?)(san|kun|chan|sama)\b", re.IGNORECASE)

DEFAULT_FORMATS = ["TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA"]
MAIN_FORMATS = {"TV", "ONA"}
SINGLE_FORMATS = {"OVA", "SPECIAL", "MOVIE"}
TV_FORMATS = {"TV", "TV_SHORT"}
EXACT = 3  # the score of an entry titled exactly like the search
# A search shortened to find an entry keeps at least this many letters, and drops at most this many words.
MIN_LETTERS = 6
MAX_DROPPED_WORDS = 3
MIN_WORDS_IN_OTHER_NAME = 3  # an other name found by its words alone needs this many, to be no coincidence
MIN_TAIL_LETTERS = 10  # a title without its first words is searched only when it keeps this many letters
ARTICLES = {"the", "a", "an"}
MIN_SUBTITLE_WORDS = 3  # a title without its first word is searched only when it keeps this many
SEASON_FORMATS = MAIN_FORMATS | {"TV_SHORT"}
# Shorter than this is a short, not a main season.
MIN_DURATION = 21
DEFAULT_SEASON_LENGTH = 12


def _is_series_start(media: Media) -> bool:
    duration = media.get("duration")
    return media.get("format") in MAIN_FORMATS and (
        duration > MIN_DURATION if duration is not None else media.get("status") == "RELEASING"
    )


def _season_formats(media: Media) -> set[str]:
    """The formats that count as seasons next to this one: shorts only in a series made of shorts."""
    return MAIN_FORMATS if _is_series_start(media) else SEASON_FORMATS


def _titles(media: Media) -> list[str]:
    return [fold_title(name) for name in [*media["title"].values(), *media.get("synonyms", [])] if name]


def _names(media: Media, title: str | None) -> bool:
    folded = fold_title(title or "")
    return any(folded in name for name in _titles(media))


def _score(media: Media, title: str) -> int:
    """3 for an entry titled exactly that, 2 for one with that synonym, 1 for one naming it, 0 for neither."""
    folded = fold_title(title)
    titles = [fold_title(name) for name in media["title"].values() if name]
    if folded in titles:
        return 3
    synonyms = [fold_title(name) for name in media.get("synonyms", [])]
    return 2 if folded in synonyms else int(any(folded in name for name in [*titles, *synonyms]))


def _has_words(media: Media, text: str) -> bool:
    """Whether one title of the entry has every word of a longer text, in any order ("Legend of Raoh: Chapter of Death")."""
    words = set(re.findall(r"\w+", text.lower()))
    return len(words) >= MIN_WORDS_IN_OTHER_NAME and any(
        words <= set(re.findall(r"\w+", name.lower()))
        for name in [*media["title"].values(), *media.get("synonyms", [])]
        if name
    )


def _has_marks(media: Media, marks: str) -> bool:
    return any(
        (m := re.search(r"([!?]+)\s*$", name)) and m[1] == marks for name in media["title"].values() if name
    )


def _has_phrase(media: Media, text: str) -> bool:
    """Whether the text is a run of whole words of one title of the entry ("of the Ring" is not in "of the Rings")."""
    words = re.findall(r"\w+", text.casefold())
    for name in [*media["title"].values(), *media.get("synonyms", [])]:
        tokens = re.findall(r"\w+", (name or "").casefold())
        if any(tokens[i : i + len(words)] == words for i in range(len(tokens) - len(words) + 1)):
            return True
    return False


def _choose(
    user_list: list[Media], everything: list[Media], title: str, *, series: bool = False, marks: str = ""
) -> list[Media]:
    """The results to read: the user's list, unless AniList's own results name the title better.

    The list is searched with the same fuzzy match, so a title it doesn't hold can still return another show
    ("K ON" -> "NHK ni Youkoso!"). The entry named best goes first (an exact title, then an exact synonym such as
    "Dr.STONE Season 3" for "Dr. STONE: NEW WORLD"), and among equals the one ending in the file's "!!" ("K-ON!!"
    is not "K-ON!"). For a file that names no format, a TV series naming it goes before a one-off
    ("Natsume's Book of Friends" is also a crossover ONA's synonym).
    """
    pool = user_list or everything
    if (
        user_list
        and everything
        and max(_score(m, title) for m in everything) > max(_score(m, title) for m in user_list)
    ):
        pool = everything
    best = max(pool, key=lambda m: _score(m, title), default=None)
    if best and _score(best, title) > _score(pool[0], title):
        pool = [best, *(m for m in pool if m is not best)]
    if marks and not _has_marks(pool[0], marks):
        same = next((m for m in pool if _score(m, title) == _score(pool[0], title) and _has_marks(m, marks)), None)
        pool = [same, *(m for m in pool if m is not same)] if same else pool
    tv = next((m for m in pool if m["format"] in TV_FORMATS and _score(m, title)), None)
    if series and tv and pool[0]["format"] not in TV_FORMATS and _score(pool[0], title) < EXACT:
        return [tv, *(m for m in pool if m is not tv)]
    return pool


class _Graph(UserDict[int, Media]):
    """Entries by ID: the search results, and what `fetch` adds for the relations they lack."""

    def __init__(self, media: list[Media], fetch: Callable[[list[int]], dict[int, Media]] | None = None) -> None:
        super().__init__((m["id"], m) for m in media)
        self.fetch = fetch

    def load(self, ids: list[int]) -> None:
        if self.fetch and (missing := [i for i in ids if i not in self]):
            try:
                self.update(self.fetch(missing))
            except AniListError as error:
                log.info("Couldn't fetch related entries: %s", error)


def _related(media: Media, relation: str, by_id: dict[int, Media]) -> list[Media]:
    ids = [e["node"]["id"] for e in media.get("relations", {}).get("edges", []) if e["relationType"] == relation]
    if isinstance(by_id, _Graph):
        by_id.load(ids)
    return [by_id[i] for i in ids if i in by_id]


def _loses_marks(media: Media, by_id: dict[int, Media], marks: str) -> bool:
    """Whether going back from this season would leave the title's "!!" behind ("K-ON!!" before "K-ON!")."""
    prequels = _related(media, "PREQUEL", by_id)
    return bool(
        marks and prequels and _has_marks(media, marks) and not any(_has_marks(p, marks) for p in prequels)
    )


def _watching(media: Media) -> bool:
    return (media.get("mediaListEntry") or {}).get("status") in {Status.CURRENT, Status.REPEATING}


def _first_season(
    node: Media,
    by_id: dict[int, Media],
    title: str,
    formats: set[str],
    stop: Callable[[Media], bool] | None = None,
) -> Media:
    """Walk back to the first season still named like the title; AniList may rank a later one first.

    A prequel with another name ("Bleach" before "Bleach: Sennen Kessen-hen") or several to choose from end the walk,
    and so does an entry that `stop` accepts. A movie or special in between is crossed.
    """
    season = cursor = node
    visited = {node["id"]}
    while not (stop and stop(season)):
        prequels = [p for p in _related(cursor, "PREQUEL", by_id) if p["id"] not in visited]
        named = [p for p in prequels if p["format"] in formats and _names(p, title)]
        step = named or [p for p in prequels if p["format"] not in formats]
        if len(step) != 1:
            break
        cursor = step[0]
        visited.add(cursor["id"])
        if cursor["format"] in formats:
            season = cursor
    return season


def _next_sequel(sequels: list[Media], title: str | None, formats: set[str]) -> Media | None:
    """The sequel to follow: a real season over a movie or special that also continues the story.

    Several seasons are told apart by the title; if that doesn't, the chain ends rather than guess.
    """
    seasons = [s for s in sequels if s["format"] in formats]
    if not seasons:
        return sequels[0]
    if len(seasons) > 1 and title:
        seasons = [s for s in seasons if _names(s, title)] or seasons
    return seasons[0] if len(seasons) == 1 else None


def main_series(
    candidates: list[Media],
    title: str | None = None,
    fetch: Callable[[list[int]], dict[int, Media]] | None = None,
    start: Media | None = None,
) -> list[Media] | None:
    """Order the main seasons by following SEQUEL relations from the first main TV/ONA.

    Args:
        candidates (list[Media]): Search results; a sequel missing from them is fetched, or ends the chain.
        title (str | None): The searched title; the chain starts at the first season named like it, not at
            the best result.
        fetch (Callable[[list[int]], dict[int, Media]] | None): Loads entries by ID, for the relations that
            the results lack.
        start (Media | None): The entry to start from instead, when the file's season or part names it.

    Returns:
        list[Media] | None: The seasons in order, or None if there is no main series among them.
            Without a full-length one, a series of shorts (TV_SHORT, short ONA) makes the chain.
    """
    by_id = _Graph(candidates, fetch)
    node = (
        start
        or next((m for m in candidates if _is_series_start(m)), None)
        or next((m for m in candidates if m["format"] in SEASON_FORMATS), None)
    )
    if node is None:
        return None
    formats = _season_formats(node)
    if title and not start:
        node = _first_season(node, by_id, title, formats)

    series = [node]
    visited = {node["id"]}
    while True:
        sequels = _related(node, "SEQUEL", by_id)
        if not sequels:
            break

        sequel = _next_sequel(sequels, title, formats)
        if sequel is None or sequel["id"] in visited:
            break
        visited.add(sequel["id"])

        node = by_id[sequel["id"]]
        if node["format"] in formats:
            series.append(node)
    return series


def locate_episode(series: list[Media] | None, absolute: int) -> tuple[Media, int] | None:
    """Find the season holding an absolute episode number.

    Returns:
        tuple[Media, int] | None: The season and the episode relative to it, or None if it is past the end.
    """
    passed = 0
    for i, season in enumerate(series or []):
        length = season.get("episodes") or DEFAULT_SEASON_LENGTH
        # A still-airing last season has no episode count; the episode must belong to it.
        open_ended = not season.get("episodes") and i == len(series) - 1
        if open_ended or passed + length >= absolute:
            return season, absolute - passed
        passed += length
    return None


def _to_info(media: Media, episode: int, file: FileInfo) -> AnimeInfo:
    entry = media.get("mediaListEntry")
    return AnimeInfo(
        anime_id=media["id"],
        anime_name=media_title(media),
        episode=episode,
        current_progress=entry["progress"] if entry else None,
        total_episodes=media.get("episodes"),
        current_status=Status(entry["status"]) if entry else None,
        guessed_name=file.query,
        absolute_episode=file.episode,
    )


def _nth_season(client: AniListClient, file: FileInfo, formats: list[str]) -> AnimeInfo | None:
    """Find "Title Season N" by counting sequels, for seasons AniList names differently ("Clannad: After Story")."""
    if file.part or (file.season or 0) <= 1:
        return None
    number = file.season
    for pool in client.search(file.title, file.year, formats):
        # A title that is the exact name of a later season ("Show Arc" S03) needs no counting
        graph = _Graph(pool, _fetcher(client))
        named = next((m for m in pool if _score(m, file.title) == EXACT and _depth(m, graph) > 1), None)
        if named and file.episode <= (named.get("episodes") or file.episode):
            return _to_info(named, file.episode, file)
        seasons = main_series(pool, fetch=_fetcher(client))
        if seasons and len(seasons) >= number:
            season = seasons[number - 1]
            if not season.get("episodes") or file.episode <= season["episodes"]:
                return _to_info(season, file.episode, file)
    return None


def _fetcher(client: AniListClient) -> Callable[[list[int]], dict[int, Media]] | None:
    return getattr(client, "related", None)


def _formats(file: FileInfo) -> list[str]:
    if file.media_format == "TV":
        return ["TV", "TV_SHORT"]
    return [file.media_format] if file.media_format else DEFAULT_FORMATS


def _first(results: list[Media], file: FileInfo) -> Media:
    """The best match; a later season goes before its specials, which "Show Season 2" also matches."""
    if (file.season or 0) > 1 and not (file.part or file.media_format):
        return next((m for m in results if m["format"] in SEASON_FORMATS), results[0])
    return results[0]


def _number_in_title(client: AniListClient, file: FileInfo) -> AnimeInfo | None:
    """Find "Mob Psycho 100" for a title read as "Mob Psycho" episode 100; only an entry naming the number counts."""
    if file.season is not None or file.part:
        return None
    number = rf"\b{file.episode}\b"
    for pool in client.search(f"{file.title} {file.episode}", file.year, _formats(file)):
        for media in pool:
            if any(re.search(number, name or "") for name in media["title"].values()):
                log.info("Episode %s is part of the title: %s", file.episode, media_title(media))
                return _to_info(media, 1, file)
    return None


def _absolute_from_base(client: AniListClient, file: FileInfo) -> AnimeInfo | None:
    """Count from the first season when a folder's season doesn't fit the episode ("Show S3/Show - 38").

    An episode that fits the first season could belong to any season, so only a remapped one is taken.
    """
    if not file.folder_season or file.query == file.title:
        return None
    log.info("Episode %s doesn't fit %s, trying the title without its folder season", file.episode, file.query)
    try:
        info = resolve(client, replace(file, season=None, part=None, folder_season=False))
    except SkipUpdate:
        return None
    return replace(info, guessed_name=file.query) if info.episode != file.episode else None


def _without_year(client: AniListClient, file: FileInfo) -> AnimeInfo | None:
    """Search again without the year, which a folder gives for one cour ("Bleach (Cour 3) (2023)/Bleach - 38")."""
    if not file.year:
        return None
    try:
        return resolve(client, replace(file, year=None))
    except SkipUpdate:
        return None


def _depth(media: Media, graph: _Graph) -> int:
    """How many main seasons lead to this one, itself included."""
    depth, cursor, seen = 1, media, {media["id"]}
    while len(prequels := [p for p in _related(cursor, "PREQUEL", graph) if p["id"] not in seen]) == 1:
        cursor = prequels[0]
        seen.add(cursor["id"])
        depth += cursor["format"] in SEASON_FORMATS  # an OVA or movie between seasons is crossed, not counted
    return depth


def _alternatives(file: FileInfo) -> list[str]:
    """Other searches for a title AniList doesn't find as written.

    AniList's search needs every word, so a season or part tacked on ("... BLOOM Season 3", "... Season 1 Part 1")
    or an English subtitle it lacks ("Grimgar Ashes and Illusions") finds nothing.
    """
    title, season, part = file.title, file.season or 0, file.part or 0
    texts = []
    if part:
        texts += [f"{title} Season {season}" if season > 1 else title]
        texts += [f"{title} Part {part}"] if part > 1 else []
    texts.append(title)
    texts.append(_HONORIFIC.sub(r"\1 \2", title))  # AniList spells "Isekai Ojisan" as "Isekai Oji-san"
    texts.append(_TIMES.sub(TIMES_SIGN, title))  # and "Durarara X2 Ketsu" with a times sign
    if file.alt_title and len(file.alt_title.split()) > 1:
        texts.append(file.alt_title)
    if file.folder_title:
        texts.append(file.folder_title)
    words = title.split()
    # A shortened search finds the show's first entry or one of its extras, so it is no guess for a file that says which
    for dropped in range(1, 0 if file.media_format else min(MAX_DROPPED_WORDS, len(words) - 2) + 1):
        shortened = " ".join(words[:-dropped])
        if len(re.findall(r"[^\W\d_]", shortened)) >= MIN_LETTERS:
            texts.append(shortened)
    # "Gintama 3 nen Z gumi Ginpachi sensei" is only "3-nen Z-gumi Ginpachi-sensei" on AniList, and
    # "Uma Musume Pretty Derby BNW no Chikai" only "Uma Musume: BNW no Chikai"; _variants checks the first word
    for dropped in range(1, len(words) - MIN_SUBTITLE_WORDS + 1):
        tail = " ".join(words[dropped:])
        if len(re.findall(r"[^\W\d_]", tail)) >= MIN_TAIL_LETTERS:
            texts.append(tail)
    return [text for text in dict.fromkeys(texts) if text != file.query]


def _variants(client: AniListClient, file: FileInfo, formats: list[str]) -> list[Media]:
    """The results of the first other search that finds an entry naming it, in batches of three per request."""
    texts = _alternatives(file)
    first = next((w for w in file.title.split() if w.casefold() not in ARTICLES), file.title)
    # AniList files the same kind of extra under either
    formats = [
        *formats,
        *(f for f in ("OVA", "SPECIAL") if file.media_format in {"OVA", "SPECIAL"} and f not in formats),
    ]
    for start in range(0, len(texts), 3):
        client.prefetch([(text, file.year) for text in texts[start : start + 3]], formats)
        for text in texts[start : start + 3]:
            user_list, everything = client.search(text, file.year, formats)
            other_name = text == file.alt_title
            # A tail of the title must be whole words of the entry's title, which must still carry the first word
            tail = text not in {file.title, file.alt_title, file.folder_title} and file.title.endswith(f" {text}")
            pool = [
                m
                for m in _choose(user_list, everything, text)
                if (_score(m, text) or (other_name and _has_words(m, text)))
                and (not tail or (_names(m, first) and _has_phrase(m, text)))
            ]
            if not file.media_format:
                pool.sort(key=lambda m: m["format"] not in SEASON_FORMATS)
            # A search without the season finds some season of the show, so count the seasons that lead there
            if pool and (file.season or 0) > 1 and not file.part and text != file.folder_title:
                graph = _Graph([*user_list, *everything], _fetcher(client))
                if _depth(pool[0], graph) != file.season:
                    continue
            if pool:
                log.info("Found by searching %r instead of %r", text, file.query)
                return pool
    return []


def _by_absolute(client: AniListClient, file: FileInfo, formats: list[str]) -> AnimeInfo | None:
    """Place the number across all seasons that the name gives beside the episode's own ("S02E12 (063)")."""
    user_list, everything = client.search(file.title, file.year, formats)
    pools = [_choose(user_list, everything, file.title)]
    if user_list:
        pools.append(everything)
    for pool in pools:
        if located := locate_episode(main_series(pool, file.title, _fetcher(client)), file.absolute or 0):
            season, relative = located
            log.info(
                "Absolute episode %s corresponds to episode %s of %s", file.absolute, relative, media_title(season)
            )
            return _to_info(season, relative, file)
    return None


def _by_arc(client: AniListClient, file: FileInfo, formats: list[str]) -> AnimeInfo | None:
    """Resolve "Show Arc" for a file in "Season 2 - Arc/", whose numbering goes on into the arc's later entries.

    The season of the folder is dropped, and an entry that doesn't name the arc is no answer.
    """
    arced = replace(file, title=f"{file.title} {file.arc}", season=None, part=None, arc=None)
    try:
        info = _resolve_title(client, arced, formats)
    except SkipUpdate:
        return None
    found = [
        m for pool in client.search(arced.query, arced.year, formats) for m in pool if m["id"] == info.anime_id
    ]
    named = (
        _names(found[0], file.arc) if found else fold_title(file.arc or "") in fold_title(info.anime_name or "")
    )
    return replace(info, guessed_name=file.query) if named else None


def _stems(media: Media) -> set[str]:
    return {fold_title(re.sub(r"\(?\b(?:19|20)\d\d\b\)?", "", name)) for name in media["title"].values() if name}


def _remake(results: list[Media], first: Media, episode: int) -> Media | None:
    """An entry with the same name and format that is long enough ("Show (2006)" has 14 episodes, "Show (2009)" 28)."""
    return next(
        (
            m
            for m in results
            if m is not first
            and m["format"] == first["format"]
            and _stems(m) & _stems(first)
            and (m.get("episodes") or 0) >= episode
        ),
        None,
    )


def resolve(client: AniListClient, file: FileInfo) -> AnimeInfo:
    """Search AniList for a file's anime, preferring the user's list.

    Nothing matching the title, or an absolute episode that fits no season, raises `SkipUpdate`.

    Args:
        client (AniListClient): AniList access.
        file (FileInfo): The parsed path.

    Returns:
        AnimeInfo: The entry and the file's episode relative to it.
    """
    formats = _formats(file)
    # The fallbacks search these again, so they share the first request
    client.prefetch([(file.query, file.year), (file.title, file.year), (file.query, None)], formats)
    if file.absolute and file.absolute != file.episode and (info := _by_absolute(client, file, formats)):
        return info
    if file.arc and (info := _by_arc(client, file, formats)):
        return info
    return _resolve_title(client, file, formats)


def _after_earlier_parts(first: Media, file: FileInfo, graph: _Graph) -> int | None:
    """The episode of a part whose files number on from the season's earlier parts ("S02E14" in "Part 2")."""
    offset, cursor = 0, first
    for _ in range((file.part or 1) - 1):
        if len(earlier := [p for p in _related(cursor, "PREQUEL", graph) if p["format"] in SEASON_FORMATS]) != 1:
            return None
        cursor = earlier[0]
        offset += cursor.get("episodes") or 0
    return file.episode - offset if offset and file.episode - offset > 0 else None


def _continued(first: Media, file: FileInfo, graph: _Graph) -> AnimeInfo | None:
    """The entry named by the title when the file's numbering goes on from its prequels ("Ketsu [28]" is episode 4)."""
    offset, cursor, seen = 0, first, {first["id"]}
    while len(earlier := [p for p in _related(cursor, "PREQUEL", graph) if p["id"] not in seen]) == 1:
        cursor = earlier[0]
        seen.add(cursor["id"])
        if cursor["format"] not in SEASON_FORMATS:
            continue
        if not cursor.get("episodes"):
            return None
        offset += cursor["episodes"]
        if 0 < file.episode - offset <= (first.get("episodes") or 0):
            log.info("Episode %s goes on from the earlier seasons of %s", file.episode, media_title(first))
            return _to_info(first, file.episode - offset, file)
    return None


def _times_sign(client: AniListClient, file: FileInfo, formats: list[str]) -> AnimeInfo | None:
    """Resolve "Show X2 Ketsu" again as "Show <times sign>2 Ketsu", when the first search found only a one-off."""
    if not _TIMES.search(file.title):
        return None
    try:
        return _resolve_title(client, replace(file, title=_TIMES.sub(TIMES_SIGN, file.title)), formats)
    except SkipUpdate:
        return None


def _numbered_extra(results: list[Media], file: FileInfo) -> AnimeInfo | None:
    """The one-episode OVA or special that AniList names after the number ("... Episode 13") of a file past the series."""
    number = re.compile(rf"(?<![\d.])episode\s*{file.episode}(?![\d.])", re.IGNORECASE)
    for media in results:
        names = [*media["title"].values(), *media.get("synonyms", [])]
        if (
            media["format"] in SINGLE_FORMATS
            and media.get("episodes") == 1
            and any(number.search(n or "") for n in names)
        ):
            log.info("Number %s is an episode of its own on AniList: %s", file.episode, media_title(media))
            return _to_info(media, 1, file)
    return None


def _movie_series(first: Media, file: FileInfo, graph: _Graph) -> AnimeInfo | None:
    """The Nth film of a series of one-episode films numbered like episodes ("Heaven's Feel - 02" is the second)."""
    if first["format"] != "MOVIE" or first.get("episodes") != 1 or file.season or file.part:
        return None
    films, seen = [first], {first["id"]}
    while len(earlier := [p for p in _related(films[0], "PREQUEL", graph) if p["id"] not in seen]) == 1:
        if earlier[0]["format"] != "MOVIE":
            break
        seen.add(earlier[0]["id"])
        films.insert(0, earlier[0])
    if file.episode > len(films):
        return None
    film = films[file.episode - 1]
    log.info("Number %s is film %s of the series", file.episode, media_title(film))
    return _to_info(film, 1, file)


def _fit(results: list[Media], first: Media, file: FileInfo, graph: _Graph) -> AnimeInfo | None:
    """The entry for the file when its episode fits the best result, a remake of it or its part; else None."""
    if file.media_format in SINGLE_FORMATS and first.get("episodes") == 1:  # "Show - 14 (Special)" is its only one
        return _to_info(first, 1, file)
    if not (first.get("episodes") and file.episode > first["episodes"]):
        return _to_info(first, file.episode, file)
    if remake := _remake(results, first, file.episode):
        return _to_info(remake, file.episode, file)
    relative = _after_earlier_parts(first, file, graph) if file.part else None
    return _to_info(first, relative, file) if relative and relative <= first["episodes"] else None


def _resolve_title(client: AniListClient, file: FileInfo, formats: list[str]) -> AnimeInfo:
    user_list, everything = client.search(file.query, file.year, formats)
    results = _choose(user_list, everything, file.query, series=not file.media_format, marks=file.marks)
    if not results and file.media_format in {
        "OVA",
        "SPECIAL",
    }:  # AniList files the same kind of extra under either
        other = "SPECIAL" if file.media_format == "OVA" else "OVA"
        results = [
            m for m in _choose(*client.search(file.query, file.year, [other]), file.query) if _score(m, file.query)
        ]
    # "Show Season 2" can match only the specials of the second season
    later_season = (file.season or 0) > 1 and not (file.part or file.media_format)
    no_season = later_season and not any(m["format"] in SEASON_FORMATS for m in results)
    if (not results or no_season) and (info := _nth_season(client, file, formats)):
        log.info("Season %s found by counting sequels: %s", file.query, info.anime_name)
        return info
    results = results or _variants(client, file, formats)
    if not results:
        if info := _absolute_from_base(client, file):
            return info
        raise SkipUpdate(f"Couldn't find an anime from this title! ({file.query}). Is it in your list?")

    first = _first(results, file)
    # "Fairy Tail (2018)" is how AniList names that series, not a later season of "Fairy Tail"
    dated = (
        not ((file.season or 0) > 1 or file.part)
        and file.year
        and next((m for m in results if _score(m, f"{file.title} ({file.year})") == EXACT), None)
    )
    first = dated or first
    seasons = _season_formats(first)
    if not (dated or (file.season or 0) > 1 or file.part) and first["format"] in seasons:
        # AniList may rank a later season first; the user's list can lack the ones between
        by_id = _Graph([*everything, *results], _fetcher(client))
        first = _first_season(
            first, by_id, file.title, seasons, lambda m: _watching(m) or _loses_marks(m, by_id, file.marks)
        )
    if info := _fit(results, first, file, _Graph([*everything, *results], _fetcher(client))):
        log.info("Final guessed anime: %s", info.anime_name)
        return info

    if info := _absolute_from_base(client, file):
        return info

    # The episode exceeds the season, so it is probably absolute. The user's list may lack some seasons.
    pools = [results] if results is everything else [results, everything]
    if file.year:
        # A year drops the earlier seasons of the chain, which the walk back needs
        undated = client.search(file.query, None, formats)[1]
        pools = [pool + [m for m in undated if m not in pool] for pool in pools]
    # With a season or part the numbering goes on from that entry, not from the first season
    title = None if file.season or file.part else file.title
    named = first if file.season or file.part else None  # the numbering goes on into the sequels of that entry
    for pool in pools:
        if located := locate_episode(main_series(pool, title, _fetcher(client), named), file.episode):
            season, relative = located
            if title and not _names(season, title):  # a title that names one season doesn't count on into the next
                continue
            info = _to_info(season, relative, file)
            log.info("Final guessed anime: %s", info.anime_name)
            log.info("Absolute episode %s corresponds to episode: %s", file.episode, relative)
            return info
    graph = _Graph([*everything, *results], _fetcher(client))
    if info := (
        _continued(first, file, graph)
        or _movie_series(first, file, graph)
        or _numbered_extra(results, file)
        or _times_sign(client, file, formats)
        or _without_year(client, file)
        or _number_in_title(client, file)
    ):
        return info
    raise SkipUpdate(f"No valid seasons found for '{file.query}'.")


def refresh(client: AniListClient, info: AnimeInfo, anime_id: int | None = None) -> AnimeInfo:
    """Re-read an entry from AniList, for missing episode counts or a corrected ID.

    Args:
        client (AniListClient): AniList access.
        info (AnimeInfo): The current info; kept as is when it has no anime ID.
        anime_id (int | None): The entry to switch to; defaults to the current one.

    Returns:
        AnimeInfo: The info with entry-level fields replaced.
    """
    target = anime_id or info.anime_id
    if target is None:
        return info

    media = client.media_by_id(target)
    entry = media.get("mediaListEntry")
    same = media["id"] == info.anime_id

    progress, status = (entry["progress"], Status(entry["status"])) if entry else (None, None)

    return replace(
        info,
        anime_id=media["id"],
        anime_name=media_title(media) or (info.anime_name if same else None),
        current_progress=progress,
        current_status=status,
        total_episodes=media.get("episodes"),
    )
