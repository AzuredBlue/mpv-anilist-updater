"""Find the AniList entry for a parsed file, including absolute episode numbers across seasons."""

import logging
import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from .api import AniListClient, media_title
from .errors import SkipUpdate
from .models import AnimeInfo, FileInfo, Status
from .release import fold_title

log = logging.getLogger(__name__)

Media = dict[str, Any]

DEFAULT_FORMATS = ["TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA"]
MAIN_FORMATS = {"TV", "ONA"}
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


def _names(media: Media, title: str | None) -> bool:
    folded = fold_title(title or "")
    return any(folded in fold_title(name) for name in media["title"].values() if name)


def _related(media: Media, relation: str, by_id: dict[int, Media]) -> list[Media]:
    edges = media.get("relations", {}).get("edges", [])
    return [by_id[e["node"]["id"]] for e in edges if e["relationType"] == relation and e["node"]["id"] in by_id]


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


def main_series(candidates: list[Media], title: str | None = None) -> list[Media] | None:
    """Order the main seasons by following SEQUEL relations from the first main TV/ONA.

    Args:
        candidates (list[Media]): Search results; a sequel missing from them ends the chain.
        title (str | None): The searched title; the chain starts at the first season named like it, not at
            the best result.

    Returns:
        list[Media] | None: The seasons in order, or None if there is no main series among them.
            Without a full-length one, a series of shorts (TV_SHORT, short ONA) makes the chain.
    """
    by_id = {m["id"]: m for m in candidates}
    node = next((m for m in candidates if _is_series_start(m)), None) or next(
        (m for m in candidates if m["format"] in SEASON_FORMATS), None
    )
    if node is None:
        return None
    formats = _season_formats(node)
    if title:
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
        seasons = main_series(pool)
        if seasons and len(seasons) >= number:
            season = seasons[number - 1]
            if not season.get("episodes") or file.episode <= season["episodes"]:
                return _to_info(season, file.episode, file)
    return None


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


def resolve(client: AniListClient, file: FileInfo) -> AnimeInfo:
    """Search AniList for a file's anime, preferring the user's list.

    Args:
        client (AniListClient): AniList access.
        file (FileInfo): The parsed path.

    Returns:
        AnimeInfo: The entry and the file's episode relative to it.

    Raises:
        SkipUpdate: If nothing matches the title, or an absolute episode fits no season.
    """
    formats = _formats(file)
    # The fallbacks below search these again, so they share the first request
    client.prefetch([(file.query, file.year), (file.title, file.year), (file.query, None)], formats)
    user_list, everything = client.search(file.query, file.year, formats)
    results = user_list or everything
    # "Show Season 2" can match only the specials of the second season
    later_season = (file.season or 0) > 1 and not (file.part or file.media_format)
    no_season = later_season and not any(m["format"] in SEASON_FORMATS for m in results)
    if (not results or no_season) and (info := _nth_season(client, file, formats)):
        log.info("Season %s found by counting sequels: %s", file.query, info.anime_name)
        return info
    if not results:
        if info := _absolute_from_base(client, file):
            return info
        raise SkipUpdate(f"Couldn't find an anime from this title! ({file.query}). Is it in your list?")

    first = _first(results, file)
    seasons = _season_formats(first)
    if not (file.season or file.part) and first["format"] in seasons:
        # AniList may rank a later season first; the user's list can lack the ones between
        by_id = {m["id"]: m for m in [*everything, *results]}
        first = _first_season(first, by_id, file.title, seasons, _watching)
    if not (first.get("episodes") and file.episode > first["episodes"]):
        info = _to_info(first, file.episode, file)
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
    for pool in pools:
        if located := locate_episode(main_series(pool, title), file.episode):
            season, relative = located
            info = _to_info(season, relative, file)
            log.info("Final guessed anime: %s", info.anime_name)
            log.info("Absolute episode %s corresponds to episode: %s", file.episode, relative)
            return info
    if info := _without_year(client, file) or _number_in_title(client, file):
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
