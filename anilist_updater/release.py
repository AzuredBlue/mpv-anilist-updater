"""Read an anime release name: title, episode, season, part, year and format.

Fansub and scene names follow a few conventions, and every rule here is a pattern seen in them. The release group
and bracketed tags are set aside, the text before the first technical token is searched for an episode marker,
and the title is whatever precedes the earliest marker. Nothing after the episode marker is interpreted, because
that is the episode's own title.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = frozenset(
    {"mkv", "mp4", "avi", "webm", "m2ts", "ts", "wmv", "flv", "mov", "ogm", "mpg", "mpeg", "m4v", "rmvb"}
)
FORMATS = {
    "OVA": "OVA",
    "OAV": "OVA",
    "OAD": "OVA",
    "ONA": "ONA",
    "Special": "SPECIAL",
    "Specials": "SPECIAL",
    "SP": "SPECIAL",
    "TVSP": "SPECIAL",
}
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
CJK_NUMBERS = {digit: number for number, digit in enumerate("一二三四五六七八九十", 1)}

_GAP = "\x00"  # stands in for a bracketed group in the free text
_LEADING = f" {_GAP}-~:"

_TRANSLATION = str.maketrans(
    {
        "\u3010": "[",
        "\u3011": "]",
        "\u2019": "'",
        "\u2018": "'",
        "\u301c": "~",
        "\u2044": " ",
        "\u2013": "-",
        "\u2014": "-",
    }
)
_BRACKET = re.compile(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}")
_ALTERNATIVES = re.compile(r"\s+[|/]\s+")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")
_HANGUL = re.compile(r"[ᄀ-ᇿ가-힯]")
_NOISE = re.compile(r"[\\/:!*?\"<>|._\-~\[\](){}\x00。、・「」『』]")
_TRAILING = re.compile(r"(?:\s+(?:v\d+|subbed|dubbed|raws?))+$", re.IGNORECASE)
_PREFIX = re.compile(r"^(?:REPACK|PROPER)\s+", re.IGNORECASE)
# "Ren'ai" separates syllables; AniList spells it "Renai" and finds nothing with the apostrophe
_SYLLABLE_BREAK = re.compile(r"(?<=[nN])'(?=[aeiouyAEIOUY])")

# " - Movie" after a title only confuses the search ("Hyakuemu - Movie").
_MOVIE = re.compile(r"\s-\s+Movie\b", re.IGNORECASE)
# Everything from the first technical token on is the release's encoding, never title or episode.
_TECH = re.compile(
    r"\b(?:\d{3,4}[pi]|\d{3,4}x\d{3,4}|4K|[xh][. ]?26[45]|HEVC|AVC|AV1|10[- ]?bits?|Hi10P?|Blu-?Ray|BDRip|BDRemux"
    r"|BD|WEB-?DL|WEB-?Rip|HDTV|DVD(?:Rip)?|Remux)\b",
    re.IGNORECASE,
)
_EXTRA = re.compile(r"\b(?:NC(?:OP|ED)\d*|Creditless|Play All)\b", re.IGNORECASE)
# Bracketed extras of a BD release: "[Menu01]", "[CM02]", "[PV]"
_EXTRA_GROUP = re.compile(r"(?:Menu|CM|PV|IV|Preview|Trailer|Making|Interview)\d*", re.IGNORECASE)
_EXTRA_FOLDER = re.compile(
    r"Extras?|Bonus|Featurettes|Trailers?|Scans|Soundtracks?|CDs?|Menus?|Creditless", re.IGNORECASE
)
# Extras named for what they are: "Shiki OP", "Jetix Opening", "Series Menu - 01"
_EXTRA_TITLE = re.compile(r"\b(?:OP|ED|PV|CM|Menu|Opening|Ending|Trailer|Storyboards?|Preview)\s*\d*$")

# Episode markers; the earliest one in the text wins, the first listed on a tie. `last` is the end of a range
# ("E01-E03", "02-03") and `ver` the "v2" of a revised release.
# "Episode 5.5", "- 06.5": recaps and specials between episodes, which AniList has no number for
# ("2.0" is audio, "- 3.33 You Can (Not) Redo" a film's number)
_DECIMAL_EPISODE = re.compile(
    r"(?:\bE(?:P|pisode)?\.?\s*|\s-\s)(?P<ep>\d+)\.(?P<dec>5)(?:v\d)?(?=\s|\x00|$)", re.IGNORECASE
)
# "S01E05", "S01E05v2", "S01E01E02", "S01E01-03"
_SEASON_EPISODE = re.compile(
    r"\bS(?P<season>\d{1,2}) ?E(?P<ep>\d{1,4})(?:\s?v(?P<ver>\d+))?(?:(?:E|[-~]E?)(?P<last>\d{1,4}))*(?!\w)",
    re.IGNORECASE,
)
# "S2 02", "S2 - 02", "Season 2 - 02": the number after a season is the episode
_SEASON_NUMBER = re.compile(
    r"\b(?:Season\s*|S)(?P<season>\d{1,2})(?:\s+|\s*-\s*)(?P<ep>\d{1,4})(?:\s?v(?P<ver>\d+))?(?!\w)", re.IGNORECASE
)
# "E05", "EP05v2", "Episode 5"
_EPISODE_WORD = re.compile(r"\b(?:E|EP|Episode)\.?\s*(?P<ep>\d{1,4})(?:v(?P<ver>\d+))?(?!\w)", re.IGNORECASE)
# "2x05": season, then episode
_SEASON_X_EPISODE = re.compile(r"\b(?P<season>\d{1,2})x(?P<ep>\d{2,3})(?:v(?P<ver>\d+))?(?!\w)", re.IGNORECASE)
# "第05话", "#05"
_EPISODE_MARK = re.compile(r"(?:第\s*|#\s*)(?P<ep>\d{1,4})(?:\s*[话話集回]|(?!\w))")
# " - 05", " - 05v2", " - 152", " - 01-02", " - 01 ~ 26": a dash with spaces, so "07-Ghost" and "K-On" never match
_DASH_EPISODE = re.compile(
    r"(?:(?<=\s)|^)[-~]\s+(?P<ep>\d{1,4})(?:\s?v(?P<ver>\d+))?(?:\s*[-~]\s*(?P<last>\d{1,4}))?(?![\w.])",
    re.IGNORECASE,
)
_MARKERS = {
    "decimal": _DECIMAL_EPISODE,
    "season+episode": _SEASON_EPISODE,
    "season x episode": _SEASON_X_EPISODE,
    "season number": _SEASON_NUMBER,
    "episode word": _EPISODE_WORD,
    "episode mark": _EPISODE_MARK,
    "dash number": _DASH_EPISODE,
}

# Without a marker: a bracketed "[05]" or "[01-12]"; a bare number after a bracket ("(2023) 153 (1080p)");
# a zero-padded number ending the title ("Title 05"); a leading "01 - Title".
_BRACKETED_EPISODE = re.compile(r"(?P<ep>\d{1,4})(?:v\d+)?(?:\s*[-~]\s*(?P<last>\d{1,4}))?", re.IGNORECASE)
_AFTER_GAP = re.compile(
    rf"{_GAP}\s*(?P<ep>\d{{1,4}})(?:v\d+)?(?:\s*[-~]\s*(?P<last>\d{{1,4}}))?(?=\s*(?:{_GAP}|\+|$))", re.IGNORECASE
)
_PADDED_EPISODE = re.compile(
    rf"(?<=\s)(?P<ep>0\d{{1,3}}|[1-9]\d)(?:v\d+)?(?:\s+(?:END|RAW))?\s*(?={_GAP}|$)", re.IGNORECASE
)
# "Detective Conan Movie 26" is part of the title
_NUMBERED_WORD = re.compile(r"\b(?:Movie|Film|Part|Pt|Vol|Volume|No|Cour)\.?\s*$", re.IGNORECASE)
_LEADING_EPISODE = re.compile(r"\s*(?P<ep>0\d{1,3})(?:v\d+)?(?:\s+-\s+|\.\s+|(?<=\b0\d)\s+)(?=\S)", re.IGNORECASE)
_LONE_NUMBER = re.compile(r"(?P<ep>\d{1,4})(?:v\d+)?", re.IGNORECASE)

# Not after an ordinal: "2nd Season 13" is the second season's thirteenth episode
_SEASON = re.compile(
    r"(?<![0-9](?:st|nd|rd|th)\s)\b(?:Season\s*|S)(?P<n>\d{1,2})\b(?:\s*-\s*S\d{1,2}\b)?", re.IGNORECASE
)
_SEASON_CJK = re.compile(r"第\s*(?P<n>[一二三四五六七八九十\d]+)\s*[季期]|(?P<k>\d+)\s*[期기]")
_PART = re.compile(r"\b(?:Part|Pt)\.?\s*(?P<n>\d{1,2}|[IVX]{1,4})\b", re.IGNORECASE)
# A disc's number, never part of the title
_VOLUME = re.compile(r"\b(?:Vol(?:ume)?|Dis[ck])\.?\s*\d+\b", re.IGNORECASE)
_YEAR = re.compile(r"(?<=\s)(?P<year>(?:19[5-9]|20[0-3])\d)(?:\s*-\s*(?:19|20)\d\d)?(?![\w-])")
# "OVA 2", "OVA 11v2", "OVA 01-19"
_FORMAT = re.compile(
    r"\b(?P<format>OVA|OAV|OAD|ONA)(?:\s?(?P<n>\d{1,2})(?:v\d+)?(?:\s?[-~]\s?(?P<last>\d{1,3}))?)?\b"
)
# "- Special 3", "SP05", and "Title Special 1" when a number follows
_SPECIAL = re.compile(
    rf"(?:(?:^|-|{_GAP})\s*|(?<=\s)(?=Specials?\s?\d))(?P<format>Specials?|SP)"
    r"(?:\s?(?P<n>\d{1,3})(?:v\d+)?(?:\s?[-~]\s?(?P<last>\d{1,3}))?)?(?!\w)"
)

# Whole bracketed groups that say something about the show
_GROUP_SEASON = re.compile(
    r"(?:Season\s*|S)(?P<season>\d{1,2})(?:\s*(?:Pt|Part)\.?\s*(?P<part>\d{1,2}))?", re.IGNORECASE
)
_GROUP_YEAR = re.compile(r"(?P<year>(?:19[5-9]|20[0-3])\d)(?:\s*-\s*(?:19|20)\d\d)?")
_GROUP_FORMAT = re.compile(r"(?P<format>OVA|OAV|OAD|ONA|TVSP|SP|Specials?)\s*(?P<n>\d{0,2})")
# Bracketed tags that can't be the title of an all-bracket name
_TAG = re.compile(r"GB|BIG5|CHT|CHS|MP4|MKV|AAC|FLAC|OPUS|Multi[- ]?Subs?|Dual[- ]?Audio", re.IGNORECASE)

# What the release itself is made of, for the trace: checksum, resolution, source, codecs, scene group
_CRC = re.compile(r"[0-9A-F]{8}", re.IGNORECASE)
_RESOLUTION = re.compile(r"\b(?:\d{3,4}[pi]|\d{3,4}x\d{3,4}|4K)\b", re.IGNORECASE)
_SOURCE = re.compile(
    r"\b(?:UHD|Blu-?Ray|BDRip|BDRemux|BD|WEB-?DL|WEB-?Rip|WEB|HDTV|DVD(?:Rip)?|Remux|CAM)\b", re.IGNORECASE
)
_CODEC = re.compile(
    r"\b(?:AAC|FLAC|Opus|AC-?3|DDP?\d?|DTS|TrueHD|PCM|MP3|Vorbis|AV1|VP9|Xvid|JOC)\b|\b\d\.\d\b", re.IGNORECASE
)
# Release notes in brackets; any other bracketed words are an alternative title: "(From the New World)"
_TAG_WORDS = re.compile(
    r"Dual|Multi|Audio|Subs?|Eng(?:lish)?|Dub(?:bed|s)?|Batch|Weekly|Uncensored|Remaster(?:ed)?|P\d+|Raws?"
    r"|[HS]ardsub|MSubs?|OmeU|OmU|OV|DF|Synchro|VOSTFR|JPN|ENG|ESP|simple|CHT|CHS|BIG5|GB|Baha|MP4|MKV",
    re.IGNORECASE,
)
_SCENE_GROUP = re.compile(r"[A-Za-z][\w-]*")
# The right side of "WEB-DL", "Dual-Audio", "MSubs-Group" and the like is no group
_NOT_A_GROUP = re.compile(r"DL|Rip|Audio|Subs?|bits?|AC-?3|JOC|Dual|Multi|Raws?", re.IGNORECASE)
_RESOLUTIONS = frozenset({360, 480, 540, 576, 720, 1080, 1440, 2160})
_MAX_YEAR_LIKE = (1950, 2039)


@dataclass(frozen=True, slots=True)
class Release:
    """What a release name says; a field is None when the name is silent about it."""

    title: str | None = None
    episode: int | None = None  # the last one of a range
    season: int | None = None
    part: int | None = None
    year: int | None = None
    media_format: str | None = None  # OVA, ONA or SPECIAL
    skip: str | None = None  # why the name must not be applied to a list entry
    # The rest of the release, for the trace
    group: str | None = None
    crc: str | None = None
    version: int | None = None
    resolution: str | None = None
    source: str | None = None
    tags: tuple[str, ...] = ()
    alt_titles: tuple[str, ...] = ()
    episode_title: str | None = None  # a last resort for the title when the name starts with its episode


@dataclass(slots=True)
class _Marker:
    name: str
    text: str
    start: int
    episode: int | None  # None for a decimal episode
    season: int | None
    version: int | None


@dataclass(slots=True)
class _Segment:
    """One alternative of the name ("Title / Other title - 05") with what was read from it."""

    title: str = ""
    episode: int | None = None
    marked: bool = False  # the episode came from a marker in the text, not a fallback
    version: int | None = None
    season: int | None = None
    part: int | None = None
    year: int | None = None
    media_format: str | None = None
    extra: bool = False
    episode_title: str = ""  # the text after a leading "01 - ", when the name has no title of its own
    groups: list[str] = field(default_factory=list)


def fold_title(title: str) -> str:
    """A title as compared across spellings: no punctuation, spacing or case."""
    return re.sub(r"\W", "", title).casefold()


def _number(value: str) -> int | None:
    return int(value) if value.isdigit() else ROMAN.get(value.upper())


def _prepare(name: str) -> str:
    name = unicodedata.normalize("NFKC", name).translate(_TRANSLATION)
    stem, dot, extension = name.rpartition(".")
    if dot and extension.lower() in VIDEO_EXTENSIONS:
        name = stem
    return name.replace("_", " ")


def _unscene(text: str) -> str:
    """Dots separate words in scene names ("Clannad.2007.S01E01"), but not in "Dr. Stone"."""
    return re.sub(r"\S+", lambda m: m.group().replace(".", " ") if m.group().count(".") >= 2 else m.group(), text)


def _extract_groups(text: str) -> tuple[str, list[str], str | None]:
    """Take the release group, then swap every bracketed group for a gap and return their contents."""
    text = text.strip()
    release_group = None
    if text.startswith("[") and "]" in text:
        end = text.find("]")
        release_group, text = text[1:end].strip() or None, text[end + 1 :]
    groups: list[str] = []

    def take(match: re.Match[str]) -> str:
        groups.append(match.group()[1:-1])
        return _GAP

    while (replaced := _BRACKET.sub(take, text)) != text:
        text = replaced
    return text, groups, release_group


def _find_marker(text: str) -> _Marker | None:
    best: tuple[str, re.Match[str]] | None = None
    for name, pattern in _MARKERS.items():
        match = pattern.search(text)
        if match and (best is None or match.start() < best[1].start()):
            best = name, match
    if best is None:
        return None
    name, match = best
    values = match.groupdict()
    return _Marker(
        name,
        match.group().strip(),
        match.start(),
        None if values.get("dec") else int(values.get("last") or values["ep"]),
        int(values["season"]) if values.get("season") else None,
        int(values["ver"]) if values.get("ver") else None,
    )


def _classify(groups: list[str]) -> dict[str, list[str]]:
    """Sort bracketed groups into what they are, for the trace and the release's tags."""
    kinds: dict[str, list[str]] = {"crc": [], "tech": [], "tags": [], "titles": [], "other": []}
    for group in (group.strip() for group in groups):
        if _CRC.fullmatch(group):
            kind = "crc"
        elif any(p.fullmatch(group) for p in (_GROUP_YEAR, _GROUP_SEASON, _GROUP_FORMAT, _BRACKETED_EPISODE)):
            kind = "other"
        elif any(p.search(group) for p in (_TECH, _RESOLUTION, _SOURCE, _CODEC)):
            kind = "tech"
        elif not re.sub(r"[\W_]", "", _TAG_WORDS.sub("", group)):
            kind = "tags"
        else:
            kind = "titles"
        kinds[kind].append(group)
    return kinds


def _scene_group(free: str) -> str | None:
    """The group of a scene name, after the last dash of the technical tail: "H.264-GRP"."""
    if not (tech := _TECH.search(free)):
        return None
    tail = re.split(rf"\s+[|/]\s+|{_GAP}", free[tech.start() :])[0].split()
    _, dash, group = tail[-1].partition("-") if tail else ("", "", "")
    if dash and _SCENE_GROUP.fullmatch(group) and not (_NOT_A_GROUP.fullmatch(group) or _TECH.fullmatch(group)):
        return group
    return None


def _year_like(value: int) -> bool:
    return _MAX_YEAR_LIKE[0] <= value <= _MAX_YEAR_LIKE[1]


def _bare_episode(match: re.Match[str]) -> int | None:
    value = int(match["last"] or match["ep"])
    return None if value in _RESOLUTIONS or (len(match["ep"]) == 4 and _year_like(value)) else value


def _group_episode(groups: list[str]) -> int | None:
    for group in groups:
        if (match := _BRACKETED_EPISODE.fullmatch(group.strip())) and (
            episode := _bare_episode(match)
        ) is not None:
            return episode
    return None


def _season_from(text: str) -> int | None:
    if match := _SEASON.search(text):
        return int(match["n"])
    if match := _SEASON_CJK.search(text):
        value = match["n"] or match["k"]
        return int(value) if value.isdigit() else CJK_NUMBERS.get(value)
    return None


def _clean_title(text: str) -> str:
    """Flatten punctuation the way the search expects and keep Latin words over CJK ones.

    CJK words go only when the Latin ones form a single run ("Title 标题"); "Re ゼロから始める異世界生活 2nd season" is
    one native title, and without its Japanese words it finds another show.
    """
    title = " ".join(_NOISE.sub(" ", _PREFIX.sub("", _SYLLABLE_BREAK.sub("", text.strip()))).split()).strip(" ,")
    if _CJK.search(title) and not _HANGUL.search(title):
        words = title.split()
        latin = [i for i, word in enumerate(words) if not _CJK.search(word)]
        if latin and latin[-1] - latin[0] + 1 == len(latin):
            title = " ".join(words[i] for i in latin)
    return _TRAILING.sub("", title)


def _analyse(text: str, groups: list[str], *, folder: bool) -> _Segment:
    """Read one alternative: where the title ends, and the markers before and after it."""
    segment = _Segment(groups=groups)
    text = _TECH.split(_MOVIE.sub(" ", text), maxsplit=1)[0]
    segment.extra = bool(
        _EXTRA.search(text)
        or any(_EXTRA.search(group) or _EXTRA_GROUP.fullmatch(group.strip()) for group in groups)
    )

    log.debug("    searched zone: %r", text)

    marker = _find_marker(text)
    region = text[: marker.start] if marker else text
    if marker:
        segment.marked, segment.episode, segment.season, segment.version = (
            True,
            marker.episode,
            marker.season,
            marker.version,
        )
        log.debug(
            "    marker: %s %r at %d: episode=%s season=%s version=%s",
            marker.name,
            marker.text,
            marker.start,
            marker.episode,
            marker.season,
            marker.version,
        )

    # Where the title stops: the earliest marker, season, part, year, format or bracketed group
    start = len(region) - len(region.lstrip(_LEADING))
    stops = {f"{marker.name} marker" if marker else "end of text": len(region)}
    if (gap := region.find(_GAP, start)) >= 0:
        stops["bracket"] = gap
    if (plus := region.find(" + ")) >= 0:
        stops["plus"] = plus
    if match := _SEASON.search(region):
        segment.season = segment.season if segment.season is not None else int(match["n"])
        stops["season"] = match.start()
    elif match := _SEASON_CJK.search(region):
        segment.season = segment.season if segment.season is not None else _season_from(match.group())
        stops["season"] = match.start()
    if match := _PART.search(region):
        segment.part = _number(match["n"])
        stops["part"] = match.start()
    if match := _VOLUME.search(region):
        stops["volume"] = match.start()
    if (match := _YEAR.search(region)) and region[: match.start()].strip():
        segment.year = int(match["year"])
        stops["year"] = match.start()
    if match := _FORMAT.search(text):
        segment.media_format = FORMATS[match["format"]]
        if match.start() < len(region):
            stops["format"] = match.start()
    elif match := _SPECIAL.search(region):
        segment.media_format = FORMATS[match["format"]]
        stops["format"] = match.start()

    if not segment.marked and not folder and (title_end := _fall_back(segment, text, region, match)) is not None:
        stops["fallback episode"] = title_end

    cut = max(start, min(stops.values()))
    log.debug("    title stops: %s -> cut at %d", stops, cut)
    segment.title = _clean_title(region[start:cut])
    segment.extra = segment.extra or (not folder and bool(_EXTRA_TITLE.search(segment.title)))
    # "Clannad Episode 49 OVA2 Another World Kyou Chapter": a disc's number, then the OVA's own title
    if marker and segment.media_format in {"OVA", "ONA"} and (fmt := _FORMAT.search(text)):
        tail = _clean_title(text[fmt.end() :].split(_GAP)[0]) if fmt.start() >= marker.start else ""
        if _LATIN.search(tail):
            log.debug("    OVA with its own title: %r after the disc number %s", tail, segment.episode)
            segment.title, segment.episode = f"{segment.title} {tail}", 1
    # "[Group] [Oshi no Ko] - 01": the title is the bracket before the marker
    if not segment.title and marker and (count := region.count(_GAP)):
        segment.title, season, part = _strip_season(_title_from_groups(groups[:count]))
        segment.season = segment.season if segment.season is not None else season
        segment.part = segment.part or part
    for group in groups:
        _read_group(segment, group.strip())
    return segment


def _trailing_episode(region: str) -> re.Match[str] | None:
    """A number ending the title; without a leading zero it needs a title before it, and not "Movie 26"."""
    match = _PADDED_EPISODE.search(region)
    if match and not match["ep"].startswith("0"):
        before = region[: match.start()]
        if not before.strip(_LEADING) or _NUMBERED_WORD.search(before):
            return None
    return match


def _fall_back(segment: _Segment, text: str, region: str, format_match: re.Match[str] | None) -> int | None:
    """Find an episode in a name without a marker.

    Returns:
        int | None: Where the title must end when the episode was part of the text, else None.
    """
    if format_match and format_match["n"]:
        segment.episode = int(format_match["last"] or format_match["n"])
    elif (episode := _group_episode(segment.groups)) is not None or (
        (match := _AFTER_GAP.search(text)) and (episode := _bare_episode(match)) is not None
    ):
        segment.episode = episode
    elif match := _trailing_episode(region):
        segment.episode = int(match["ep"])
        return match.start()
    elif match := _LEADING_EPISODE.match(region) or _LONE_NUMBER.fullmatch(region.replace(_GAP, " ").strip()):
        # The name starts with the episode, so it has no title of its own
        segment.episode = int(match["ep"])
        if match.re is _LEADING_EPISODE:
            segment.episode_title = _clean_title(region[match.end() :].split(_GAP, maxsplit=1)[0])
        return 0
    return None


def _read_group(segment: _Segment, group: str) -> None:
    if match := _GROUP_SEASON.fullmatch(group):
        segment.season = segment.season if segment.season is not None else int(match["season"])
        if match["part"] and segment.part is None:
            segment.part = int(match["part"])
    elif (match := _GROUP_YEAR.fullmatch(group)) and segment.year is None:
        segment.year = int(match["year"])
    elif (match := _GROUP_FORMAT.fullmatch(group)) and segment.media_format is None:
        segment.media_format = FORMATS[match["format"]]
        if match["n"] and not segment.marked and segment.episode is None:
            segment.episode = int(match["n"])


def _title_from_groups(groups: list[str]) -> str:
    """Names made only of brackets ("[Group][国漫][Title][2025]...") keep the title in one of them."""
    for group in groups:
        words = _clean_title(group)
        if (
            len(_LATIN.findall(words)) >= 3
            and not _TECH.search(words)
            and not _TAG.fullmatch(words)
            and not _CRC.fullmatch(words)
        ):
            return words
    return ""


def _strip_season(title: str) -> tuple[str, int | None, int | None]:
    """Take "S2" and "Part 2" out of a title found in a bracket, where `_analyse` never looked."""
    season = part = None
    if match := _SEASON.search(title):
        season, title = int(match["n"]), title[: match.start()] + title[match.end() :]
    if match := _PART.search(title):
        part, title = _number(match["n"]), title[: match.start()] + title[match.end() :]
    return " ".join(title.split()), season, part


def parse_release(name: str, *, folder: bool = False) -> Release:
    """Read one filename or folder name.

    Args:
        name (str): The name without its directory; a known video extension is dropped.
        folder (bool): Whether this is a folder name, which has no episode of its own.

    Returns:
        Release: Title, episode, season, part, year and format, and why it must be skipped if it must.
    """
    text = _prepare(name)
    decimal = bool(_DECIMAL_EPISODE.search(text))
    free, groups, release_group = _extract_groups(_unscene(text))
    kinds = _classify(groups)
    release_group = release_group or _scene_group(free)
    log.debug(
        "parse %r (folder=%s)\n  release group: %r\n  prepared: %r\n  free text: %r\n"
        "  brackets: crc=%s tech=%s tags=%s alt titles=%s other=%s",
        name,
        folder,
        release_group,
        text,
        free,
        kinds["crc"],
        kinds["tech"],
        kinds["tags"],
        kinds["titles"],
        kinds["other"],
    )

    # Alternatives ("Title / 标题 - 02", "Title | Other title") each get their own groups
    segments, offset = [], 0
    for part in _ALTERNATIVES.split(free):
        count = part.count(_GAP)
        log.debug("  alternative %r", part.strip())
        segments.append(_analyse(part, groups[offset : offset + count], folder=folder))
        log.debug("    -> %s", segments[-1])
        offset += count

    marked = next((i for i, segment in enumerate(segments) if segment.marked), None)
    if marked is None:
        marked = next((i for i, segment in enumerate(segments) if _LATIN.search(segment.title)), 0)
    used = segments[: marked + 1]

    title = next((segment.title for segment in used if _LATIN.search(segment.title)), "")
    title = title or next((segment.title for segment in used if segment.title), "")
    grouped_season = grouped_part = None
    if not title and not used[marked].marked:
        title, grouped_season, grouped_part = _strip_season(
            _title_from_groups([group for segment in used for group in segment.groups])
        )

    episode = (
        used[marked].episode
        if used[marked].marked
        else next((s.episode for s in used if s.episode is not None), None)
    )
    # Without an episode the name describes a pack or a movie, and any alternative may carry the details
    pool = used if any(s.marked for s in segments) else segments
    season = next((s.season for s in pool if s.season is not None), grouped_season)
    part = next((s.part for s in pool if s.part is not None), grouped_part)
    year = next((s.year for s in pool if s.year is not None), None)
    # A format can sit in a trailing alternative: "... | Title | Other title (S02E00) (OVA 2)"
    media_format = next((s.media_format for s in pool if s.media_format), None) or next(
        (FORMATS[m["format"]] for group in groups if (m := _GROUP_FORMAT.fullmatch(group.strip()))), None
    )

    skip = None
    if folder:
        skip = "an extra, not an episode" if _EXTRA_FOLDER.fullmatch(text.strip()) else None
    elif decimal:
        skip = "a special between episodes"
    elif any(segment.extra for segment in used):
        skip = "an extra, not an episode"
    elif season == 0 and media_format is None:
        skip = "a season 0 special"
    body = text[text.find("]") + 1 :] if release_group and text.lstrip().startswith("[") else text
    release = Release(
        title or None,
        episode,
        season,
        part,
        year,
        media_format,
        skip,
        group=release_group,
        crc=kinds["crc"][0] if kinds["crc"] else None,
        version=used[marked].version if used[marked].marked else None,
        resolution=m.group() if (m := _RESOLUTION.search(body)) else None,
        source=m.group() if (m := _SOURCE.search(body)) else None,
        tags=tuple(kinds["tags"]),
        alt_titles=tuple(kinds["titles"]),
        episode_title=next((s.episode_title for s in used if s.episode_title), None),
    )
    log.debug("  result: %s", release)
    return release
