"""Read an anime release name: title, episode, season, part, year and format.

Fansub and scene names follow a few conventions, and every rule here is a pattern seen in them. The release group
and bracketed tags are set aside, the text before the first technical token is searched for an episode marker,
and the title is whatever precedes the earliest marker. Nothing after the episode marker is interpreted, because
that is the episode's own title.

Compared with other parsers on 1,850 files (paths with folders) from 558 nyaa torrents: 1,743 labeled episodes
and 81 extras, all resolved on AniList by the same code, so only the parser differs. This is a blind benchmark
sampled across random torrents.

    parser                          correct  wrong anime  wrong episode  not found
    this                            88.6%    4.6%         1.0%           5.7%
    old                             82.5%    9.0%         2.4%           6.1%
    aniparse + folder title         82.0%    10.0%        2.2%           5.7%
    anitopy + folder title          78.5%    5.9%         1.0%           14.6%
    aniparser + folder title        71.5%    14.6%        2.1%           11.9%
    guessit, filename               64.6%    16.9%        2.6%           15.9%
    guessit, full path              52.4%    11.2%        9.6%           26.7%
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
    "SPE": "SPECIAL",
    "EX": "SPECIAL",
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
        "`": "'",
        "\u00b4": "'",
        "\u301c": "~",
        "\u2044": " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }
)
_BRACKET = re.compile(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}")
_ALTERNATIVES = re.compile(r"\s+[|/]\s+")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯؀-ۿ]")
# "中文配": a dub's language, not a title word
_DUB_TAG = re.compile(r"(?:^|\s)[一-鿿]*配(?=\s|$)")
_HANGUL = re.compile(r"[ᄀ-ᇿ가-힯]")
_NOISE = re.compile(r"[\\/:!*?\"<>|._\-~\[\](){}\x00。、・「」『』]")
_TRAILING = re.compile(
    r"(?:\s+(?:v\d+|subbed|dubbed|raws?|vostfr|f?hd|remaster(?:ed)?|dual audio|complete (?:version|edition)))+$",
    re.IGNORECASE,
)
# A trailing "TV" or "Movie" tells the format, as AniList's "School Days (TV)" does
_FORMAT_TAIL = re.compile(r"\s+(?:(?P<tv>TV)|(?:the\s+)?Movies?)$", re.IGNORECASE)
_PREFIX = re.compile(r"^(?:REPACK|PROPER)\s+", re.IGNORECASE)
_INDEXED = re.compile(r"^\d{1,2}\.\s+(?=\D)")
# "Ren'ai" separates syllables; AniList spells it "Renai" and finds nothing with the apostrophe
_SYLLABLE_BREAK = re.compile(r"(?<=[nN])'(?=[aeiouyAEIOUY])")

# " - Movie" after a title only confuses the search ("Hyakuemu - Movie"), so it is dropped and becomes the format.
_MOVIE = re.compile(r"\s-\s+Movie\b", re.IGNORECASE)
# "Ketsu + OVA", "S01-S02+OVA+SP": what else the pack holds, not this file's format
_PACK = re.compile(r"\+\s*(?=(?:OVA|ONA|OAD|SP|Specials?|Movies?|Extras?)\b)", re.IGNORECASE)
# Everything from the first technical token on is the release's encoding, never title or episode.
_TECH = re.compile(
    r"\b(?:\d{3,4}[pi]|\d{3,4}x\d{3,4}|4K|[xh][. ]?26[45]|HEVC|AVC|AV1|10[- ]?bits?|Hi10P?|Blu-?Ray|BDRip|BDRemux"
    r"|BD|WEB-?DL|WEB-?Rip|HDTV|DVD(?:Rip)?|Remux)\b",
    re.IGNORECASE,
)
_EXTRA = re.compile(
    r"\b(?:NC(?:OP|ED)\d*[a-z]?|(?:OP|ED)\d+(?:v\d+)?[a-z]?|(?:OP|ED)v\d+|Creditless|Play All"
    r"|Clean\s+(?:Opening|Ending)|(?:Opening|Ending)\s+Clean)\b",
    re.IGNORECASE,
)
# Bracketed extras of a BD release: "[Menu01]", "[CM02]", "[PV]"
_EXTRA_GROUP = re.compile(
    r"(?:Menu|CM|PV|IV|Preview|Trailer|Making|Interview|Audio Drama|Drama CD|Behind the Scenes|Commentary)"
    r"\s*\d*(?:\.\d+)?",
    re.IGNORECASE,
)
_EXTRA_FOLDER = re.compile(
    r"Extras?|Bonus|Featurettes|Trailers?|Scans|Soundtracks?|CDs?|Menus?|Creditless", re.IGNORECASE
)
# "Show/Film/": the folder names the format of the files in it
_MOVIE_FOLDER = re.compile(r"Movies?|Films?", re.IGNORECASE)
# Extras named for what they are: "Shiki OP", "Jetix Opening", "Series Menu - 01"
_EXTRA_TITLE = re.compile(
    r"\b(?:OP|ED|PV|CM|Menu|Opening|Ending|Trailer|Storyboards?|Preview)(?:\s*\d+(?:v\d+)?[a-z]?)?$", re.IGNORECASE
)

# Episode markers; the earliest one in the text wins, the first listed on a tie. `last` is the end of a range
# ("E01-E03", "02-03") and `ver` the "v2" of a revised release.
# "Episode 5.5", "- 06.5": recaps and specials between episodes, which AniList has no number for
# ("2.0" is audio, "- 3.33 You Can (Not) Redo" a film's number)
# ("Magikarte 02.5", "OVA3.5", and "- 04.1" after a zero-padded number)
_DECIMAL_EPISODE = re.compile(
    r"(?:\bE(?:P|pisode)?\.?\s*|\s-\s|\bOVA\s?|(?<=\s)(?=0\d{1,2}\.))(?P<ep>\d+)\.(?P<dec>5|(?<=0\d\.)\d)(?:v\d)?"
    r"(?=[\s(\[\x00]|$)",
    re.IGNORECASE,
)
# "S01E05", "S01E05v2", "S01E01E02", "S01E01-03"
_SEASON_EPISODE = re.compile(
    r"(?:\b|(?<=[a-z]))S(?P<season>\d{1,2}) ?E(?P<ep>\d{1,4})(?:\s?v(?P<ver>\d+))?(?:(?:E|[-~]E?)(?P<last>\d{1,4}))*(?!\w)",
    re.IGNORECASE,
)
# "s01ova02"
_SEASON_OVA = re.compile(r"\bS(?P<season>\d{1,2})(?P<format>OVA|OAD|SP)(?P<ep>\d{1,3})(?!\w)", re.IGNORECASE)
# "Vol 2 06 Title": the number after a disc is the episode
_VOLUME_EPISODE = re.compile(r"\bVol(?:ume)?\.?\s*\d+\s+(?P<ep>0\d|[1-9]\d)(?!\w)", re.IGNORECASE)
# "S2 02", "S2 - 02", "Season 2 - 02": the number after a season is the episode
_SEASON_NUMBER = re.compile(
    r"\b(?:Season\s*|S)(?P<season>\d{1,2})(?:\s+|\s*-\s*)(?P<ep>\d{1,4})(?:\s?v(?P<ver>\d+))?(?!\w)", re.IGNORECASE
)
# "E05", "EP05v2", "Episode 5"
# Also "Episode 121-125", "Ep. 1-5" and "Episodio 009"
_EPISODE_WORD = re.compile(
    r"\b(?:E|EP|Episode|Episodio|Episódio|Épisode|Folge)\.?\s*(?P<ep>\d{1,4})(?:v(?P<ver>\d+))?"
    r"(?:[-~](?:E(?:P|pisode)?\.?\s*)?(?P<last>\d{1,4}))?(?!\w)",
    re.IGNORECASE,
)
# "2x05": season, then episode
_SEASON_X_EPISODE = re.compile(r"\b(?P<season>\d{1,2})x(?P<ep>\d{2,3})(?:v(?P<ver>\d+))?(?!\w)", re.IGNORECASE)
# "第05话", "#05", "#01v2", "#1-4"
_EPISODE_MARK = re.compile(
    r"(?:第\s*|#\s*)(?P<ep>\d{1,4})(?:v(?P<ver>\d+))?(?:[-~](?P<last>\d{1,4}))?(?:\s*[话話集回]|(?!\w))"
)
# " - 05", " - 05v2", " - 111c", " - 152", " - 01-02", " - 01+02", " - 01 ~ 26": a dash with spaces, so "07-Ghost"
# and "K-On" never match
_DASH_HEAD = r"(?:(?<=\s)|^)[-~]\s+(?P<ep>\d{1,4})[a-c]?(?:\s?v(?P<ver>\d+))?"
_DASH_EPISODE = re.compile(
    rf"{_DASH_HEAD}(?:(?:\s*[-~]\s*|\+)(?P<last>\d{{1,4}})(?:[a-c]|v\d+)?)?(?![\w.])", re.IGNORECASE
)
_DASH_SINGLE = re.compile(rf"{_DASH_HEAD}(?![\w.])", re.IGNORECASE)
# "Title  -011 - Subtitle": the dash is glued to a zero-padded number, which keeps "-2000" and "-1" out
_GLUED_DASH_EPISODE = re.compile(r"(?<=\s)[-~](?P<ep>0\d{1,2})(?=\s|$)")
_MARKERS = {
    "decimal": _DECIMAL_EPISODE,
    "season+episode": _SEASON_EPISODE,
    "season ova": _SEASON_OVA,
    "season x episode": _SEASON_X_EPISODE,
    "season number": _SEASON_NUMBER,
    "episode word": _EPISODE_WORD,
    "episode mark": _EPISODE_MARK,
    "volume episode": _VOLUME_EPISODE,
    "dash number": _DASH_EPISODE,
    "glued dash number": _GLUED_DASH_EPISODE,
}

# Without a marker: a bracketed "[05]" or "[01-12]"; a bare number after a bracket ("(2023) 153 (1080p)");
# a zero-padded number ending the title ("Title 05"); a leading "01 - Title".
# Also "[Ep.24]", "[20 of 25]" and "[24(END)]"
# and "[02(14)]", the season's own number with the absolute one in parentheses
_BRACKETED_EPISODE = re.compile(
    r"(?:E(?:p|pisode)?\.?\s*)?(?P<ep>\d{1,4})(?:v\d+)?(?:\s*[-~]\s*(?P<last>\d{1,4}))?(?:\s+of\s+\d+)?"
    r"(?:\s*\(?(?:END|FINAL)\)?)?(?:\((?P<absolute>\d{1,4})\))?",
    re.IGNORECASE,
)
# "(S4-24)": a season's own number next to the absolute one
# and "[04x01]"
_GROUP_SEASON_EPISODE = re.compile(
    r"(?:S(?P<season>\d{1,2})\s*[-x]|(?P<other>\d{1,2})x)\s*(?P<ep>\d{1,4})", re.IGNORECASE
)
_AFTER_GAP = re.compile(
    rf"{_GAP}\s*(?P<ep>\d{{1,4}})(?:v\d+)?(?:\s*[-~]\s*(?P<last>\d{{1,4}}))?(?=\s*(?:{_GAP}|\+|$))", re.IGNORECASE
)
# "Title 05", "Title 225", "Title 111c", "Title 13-15", "Title 603 VOSTFR HD"
_TAIL_WORDS = r"(?:END|RAW|FINAL|VOSTFR|VF|FHD|HD|FANSUB|SUBBED|PS VITA)"
_PADDED_EPISODE = re.compile(
    rf"(?:(?<=\s)|(?<=[A-Za-z]\.))(?P<ep>0\d{{1,3}}|[1-9]\d{{0,3}})[a-c]?(?:\s?v\d+)?(?:[-~](?P<last>\d{{1,4}}))?"
    rf"(?P<more>(?:\s*&\s*\d{{1,4}}(?:[-~]\d{{1,4}})?)*)(?:\s+{_TAIL_WORDS})*\s*(?={_GAP}|$)",
    re.IGNORECASE,
)
# "Detective Conan Movie 26" is part of the title
_NUMBERED_WORD = re.compile(r"\b(?:Movies?|Film|Part|Pt|Vol|Volume|No|Cour)\.?\s*$|&\s*$", re.IGNORECASE)
_LEADING_EPISODE = re.compile(
    r"\s*(?P<ep>0\d{1,3}|[1-9]\d{1,2})(?:v\d+)?(?:\s+-\s+|\.\s+|(?<=\b0\d)\s+)(?=\S)", re.IGNORECASE
)
# "Monster 34 - At the End of Darkness", "The Animatrix 08 A Detective Story"
_TITLE_NUMBER_DASH = re.compile(r"(?<=\S\s)(?P<ep>0\d{1,2}|[1-9]\d{1,3})\s+-\s+(?=[^\W\d])")
_QUOTED_EPISODE = re.compile(
    r"(?<=\S\s)(?P<ep>0\d{1,2}|[1-9]\d{1,2})\s+['\"“](?P<title>.+?)['\"”](?:\s*(?:$|\x00))"
)
_PADDED_WORDS = re.compile(r"(?<=\S\s)(?P<ep>0\d{1,2})\s+(?=[A-Z])")
_LONE_NUMBER = re.compile(r"(?P<ep>\d{1,4})(?:v\d+)?", re.IGNORECASE)

# Not after an ordinal: "2nd Season 13" is the second season's thirteenth episode. "Season 1-3", "S1-S2" and "s01s02"
# are the last season of a pack.
_SEASON_WORDS = r"(?:(?:Season|Temporada|Saison|Staffel)\s*|S)"
_SEASON = re.compile(
    rf"(?<![0-9](?:st|nd|rd|th)\s)\b{_SEASON_WORDS}(?P<n>\d{{1,2}})(?:(?:\s*-\s*S|(?<=\d)S)(?P<last>\d{{1,2}}))?"
    r"(?:P(?P<p>\d{1,2}))?\b",
    re.IGNORECASE,
)
_SEASON_ORDINAL = re.compile(r"\b(?P<n>\d{1,2})(?:st|nd|rd|th)\s+Season\b", re.IGNORECASE)
_SEASON_CJK = re.compile(r"第\s*(?P<n>[一二三四五六七八九十\d]+)\s*[季期]|(?P<k>\d+)\s*[期기]")
_ROMAN_SEASON = {"II": 2, "III": 3, "IV": 4}
_SEASON_ROMAN = re.compile(
    r"(?<!\bLupin)(?<!\bPart)(?<!\bPt)(?<!\bPart\.)(?<!\bPt\.)(?:\s+|-)(?P<roman>II|III|IV)\s*$", re.ASCII
)
_PART = re.compile(r"\b(?:Part|Pt)\.?\s*(?P<n>\d{1,2}|[IVX]{1,4})\b", re.IGNORECASE)
# A disc's number, never part of the title
_VOLUME = re.compile(r"\b(?:Vol(?:ume)?|Dis[ck])\.?\s*\d+(?:v\d+)?\b", re.IGNORECASE)
# "Seasons 1-4 + OVA + Junior High" lists more releases, "Norn9 - Norn + Nonetto" is one title
_PLUS = re.compile(r"\s\+\s(?=\d|S\d|OVA|ONA|OAD|Special|Movie|Part)")
_YEAR = re.compile(r"(?<=\s)(?P<year>(?:19[5-9]|20[0-3])\d)(?:\s*-\s*(?:19|20)\d\d)?(?![\w-])")
# "OVA 2", "OVA 11v2", "OVA 01-19"
_FORMAT = re.compile(
    r"\b(?P<format>(?i:OVA|OAV|OAD|ONA))(?:\s?(?P<n>\d{1,2})(?:v\d+)?(?:\s?[-~]\s?(?P<last>\d{1,3}))?)?\b"
)
# "- Special 3", "SP05", and "Title Special 1" when a number follows
_SPECIAL = re.compile(
    rf"(?:(?:^|-|{_GAP})\s*|(?<=\s)(?=Specials?\s?\d))(?P<format>Specials?(?!\s+[A-Za-z])|(?i:SPE?)|EX)"
    r"(?:\s?(?P<n>\d{1,3})(?:v\d+)?(?:\s?[-~]\s?(?P<last>\d{1,3}))?)?(?!\w)"
)

# Whole bracketed groups that say something about the show
_GROUP_SEASON = re.compile(
    rf"{_SEASON_WORDS}(?P<season>\d{{1,2}})(?:\s*(?:Pt|Part)\.?\s*(?P<part>\d{{1,2}}))?", re.IGNORECASE
)
# "(Season 1-4 + Kanketsu-hen + OVA)": a pack, the last season counts
_GROUP_SEASONS = re.compile(
    r"(?:(?:Season|Temporada|Saison|Staffel)\s*\d{1,2}\s*-\s*S?|S\d{1,2}\s*-\s*S)(?P<season>\d{1,2})\b",
    re.IGNORECASE,
)
_GROUP_YEAR = re.compile(r"(?P<year>(?:19[5-9]|20[0-3])\d)(?:\s*-\s*(?:19|20)\d\d)?")
_GROUP_FORMAT = re.compile(r"(?P<format>OVA|OAV|OAD|ONA|TVSP|SP|Specials?)\s*(?P<n>\d{0,2})")
# Bracketed tags that can't be the title of an all-bracket name
_TAG = re.compile(r"GB|BIG5|CHT|CHS|MP4|MKV|AAC|FLAC|OPUS|Multi[- ]?Subs?|Dual[- ]?Audio", re.IGNORECASE)

# What the release itself is made of, for the trace: checksum, resolution, source, codecs, scene group
_CRC = re.compile(r"[0-9A-F]{8}", re.IGNORECASE)
_GLUED_PREFIX = re.compile(r"[^\s\[\]()]+?[.\s]*(?=\[(?P<group>[^\]]+)\][\s.-]*[A-Za-z])")
_ENCODED = re.compile(r"\bencoded\b|\bripped\b|\bsubbed\b", re.IGNORECASE)
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
    media_format: str | None = None  # OVA, ONA, SPECIAL, TV or MOVIE
    absolute: int | None = None  # the number across all seasons, when the name gives it next to the episode's own
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
    marks: str = ""  # the "!!" of "K-ON!!", which tells it from "K-ON!"


@dataclass(slots=True)
class _Marker:
    name: str
    text: str
    start: int
    end: int
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
    absolute: int | None = None
    extra: bool = False
    episode_title: str = ""  # the text after a leading "01 - ", when the name has no title of its own
    marks: str = ""  # the "!!" the title ends with, which cleaning drops
    groups: list[str] = field(default_factory=list)


_TITLE_MARKS = re.compile(r"([!?]+)\s*$")


def fold_title(title: str) -> str:
    """A title as compared across spellings: no punctuation, spacing or case."""
    return re.sub(r"\W", "", title).casefold()


def _format_of(name: str) -> str:
    return FORMATS.get(name) or FORMATS[name.upper()]


def _format_word(match: re.Match[str] | None, text: str) -> re.Match[str] | None:
    """Drop a format word not in capitals that is the title's own word ("Ona - 01", "Sample Show ova no hon")."""
    if match is None or match["format"].isupper() or match["format"].lower().startswith("special"):
        return match
    if not text[: match.start()].strip(_LEADING):
        return None
    rest = text[match.end() :].lstrip()
    return None if not match["n"] and re.match(r"[A-Za-z]", rest) and not _EXTRA.match(rest) else match


def _number(value: str) -> int | None:
    return int(value) if value.isdigit() else ROMAN.get(value.upper())


def _prepare(name: str) -> str:
    name = unicodedata.normalize("NFKC", name.translate(_TRANSLATION))
    stem, dot, extension = name.rpartition(".")
    if dot and extension.lower() in VIDEO_EXTENSIONS:
        name = stem
    # "[Group Show Name 05 1080p]": the whole name in one bracket
    if wrapped := re.fullmatch(r"\s*\[([^\[\]]+)\]\s*", name):
        name = wrapped[1]
    # "toradora!-03-your-song": hyphens for spaces
    if " " not in name and "[" not in name and re.search(r"-\d{1,4}-", name):
        name = name.replace("-", " - ")
    return name.replace("_", " ")


def _unscene(text: str) -> str:
    """Dots separate words in scene names ("Clannad.2007.S01E01"), but not in "Dr. Stone"."""
    return re.sub(r"\S+", lambda m: m.group().replace(".", " ") if m.group().count(".") >= 2 else m.group(), text)


def _extract_groups(text: str) -> tuple[str, list[str], str | None]:
    """Take the release group, then swap every bracketed group for a gap and return their contents."""
    text = text.strip()
    release_group = None
    # "EvoBot.[Group] Title", "37 [Group] Title": something glued before the group is no part of the name
    if (glued := _GLUED_PREFIX.match(text)) and _classify([glued["group"]])["titles"]:
        text = text[glued.end() :]
    if text.startswith("["):
        # "[Mites[ Title": a "[" followed by a space is a typo for "]"
        if m := re.match(r"^\[(?P<group>[^\[\]]+)(?:\]|\[(?=\s))", text) or re.match(
            r"^\[(?P<group>[^\]]*)\]", text
        ):
            first = m["group"].strip()
            if not (_BRACKETED_EPISODE.fullmatch(first) or _GROUP_SEASON_EPISODE.fullmatch(first)):
                release_group, text = first or None, text[m.end() :]
    elif (broken := re.match(r"\s*\]?([^\[\]]+)\]", text)) and "[" not in text[: broken.end()]:
        release_group, text = broken[1].strip() or None, text[broken.end() :]  # "]group] Title"
    groups: list[str] = []

    def take(match: re.Match[str]) -> str:
        groups.append(match.group()[1:-1])
        return _GAP

    while (replaced := _BRACKET.sub(take, text)) != text:
        text = replaced
    return text, groups, release_group


def _dash_match(text: str) -> re.Match[str] | None:
    """The " - 05" that ends the title.

    A number followed by words belongs to the title when another one follows ("Fairy Tail - 100 Years Quest - 05"),
    and a spaced range must end the text ("Naruto - 116 - 360 Degrees" is episode 116).
    """
    matches = []
    for match in _DASH_EPISODE.finditer(text):
        rest = text[match.end() :].lstrip()
        if (
            match["last"]
            and rest
            and rest[0] not in f"{_GAP}+"
            and re.search(r"\s", text[match.end("ep") : match.start("last")])
        ):
            match = _DASH_SINGLE.match(text, match.start()) or match  # ruff: ignore[redefined-loop-name]
            rest = text[match.end() :].lstrip()
        # "- 3-nen Z-gumi - 02": a hyphen glued to a word goes on with the title
        matches.append((match, not rest or rest[0] in f"{_GAP}+" or (rest[0] in "-~" and not rest[1:2].isalpha())))
    return next((m for m, settled in matches if settled), matches[0][0] if matches else None)


def _find_marker(text: str) -> _Marker | None:
    best: tuple[str, re.Match[str]] | None = None
    for name, pattern in _MARKERS.items():
        match = _dash_match(text) if pattern is _DASH_EPISODE else pattern.search(text)
        if match and (best is None or match.start() < best[1].start()):
            best = name, match
    if best is None:
        return None
    name, match = best
    # "Fairy Tail - 100 Years Quest - S01E01": a number followed by words is part of the title when a real marker follows
    if name == "dash number" and text[match.end() :].lstrip()[:1].isalpha():
        later = _SEASON_EPISODE.search(text, match.end())
        name, match = ("season+episode", later) if later else (name, match)
    values = match.groupdict()
    return _Marker(
        name,
        match.group().strip(),
        match.start(),
        match.end(),
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
        elif _GROUP_SEASONS.match(group) or any(
            p.fullmatch(group)
            for p in (_GROUP_YEAR, _GROUP_SEASON, _GROUP_FORMAT, _BRACKETED_EPISODE, _GROUP_SEASON_EPISODE)
        ):
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


def _last_season(match: re.Match[str]) -> int:
    return int(match.groupdict().get("last") or match["n"])


def _season_from(text: str) -> int | None:
    if match := _SEASON_ORDINAL.search(text) or _SEASON.search(text):
        return _last_season(match)
    if match := _SEASON_CJK.search(text):
        value = match["n"] or match["k"]
        return int(value) if value.isdigit() else CJK_NUMBERS.get(value)
    if match := _SEASON_ROMAN.search(text):
        return _ROMAN_SEASON[match["roman"].upper()]
    return None


def _clean_title(text: str) -> str:
    """Flatten punctuation the way the search expects and keep Latin words over CJK ones.

    CJK words go only when the Latin ones form a single run ("Title 标题"); "Re ゼロから始める異世界生活 2nd season" is
    one native title, and without its Japanese words it finds another show.
    """
    title = " ".join(_NOISE.sub(" ", _PREFIX.sub("", _SYLLABLE_BREAK.sub("", text.strip()))).split()).strip(" ,")
    title = " ".join(_DUB_TAG.sub(" ", title).split())
    if not re.search(r"\w", title):  # "★"
        return ""
    if _CJK.search(title) and not _HANGUL.search(title):
        words = title.split()
        latin = [i for i, word in enumerate(words) if not _CJK.search(word)]
        # "Re" alone is not a title: the Japanese words make it one
        if (
            latin
            and latin[-1] - latin[0] + 1 == len(latin)
            and len(_LATIN.findall(" ".join(words[i] for i in latin))) > 2
        ):
            title = " ".join(words[i] for i in latin)
    return _TRAILING.sub("", title)


def _kept_groups(region: str, groups: list[str]) -> set[int]:
    """The brackets that belong to the title: attached to a word ("Mezzo(DSA)") or followed by more of it."""
    kept = set()
    positions = [i for i, char in enumerate(region) if char == _GAP]
    for index, position in enumerate(positions):
        if index >= len(groups) or not _classify([groups[index]])["titles"]:
            continue
        end = positions[index + 1] if index + 1 < len(positions) else len(region)
        after = region[position + 1 : end].strip(_LEADING)
        # "Nekomonogatari (Kuro) 01": a lone word before the episode tells two shows apart
        lone_word = re.fullmatch(r"[A-Za-z]+", groups[index].strip()) and re.match(r"\d{1,4}\b", after)
        if (
            region[position - 1 : position].isalnum()
            or (_LATIN.match(after) and re.sub(r"[\W_]", "", _TAG_WORDS.sub("", after)))
            or lone_word
        ):
            kept.add(index)
    return kept


def _analyse(text: str, groups: list[str], *, folder: bool) -> _Segment:
    """Read one alternative: where the title ends, and the markers before and after it."""
    segment = _Segment(groups=groups)
    text = _TECH.split(text, maxsplit=1)[0]
    movie = _MOVIE.search(text)
    text = _MOVIE.sub(" ", text)
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
        if marker.name == "season ova":
            segment.media_format = _format_of(_SEASON_OVA.search(marker.text)["format"])
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
    kept = _kept_groups(region, groups)
    gaps = [i for i, char in enumerate(region) if char == _GAP]
    if gap := next((pos for index, pos in enumerate(gaps) if pos >= start and index not in kept), None):
        stops["bracket"] = gap
    if plus := _PLUS.search(region):
        stops["plus"] = plus.start()
    if extra := _EXTRA.search(region):
        stops["extra"] = extra.start()
    if match := _SEASON_ORDINAL.search(region) or _SEASON.search(region):
        segment.season = segment.season if segment.season is not None else _last_season(match)
        if match.groupdict().get("p"):  # "S01P02"
            segment.part = int(match["p"])
        stops["season"] = match.start()
    elif match := _SEASON_CJK.search(region):
        segment.season = segment.season if segment.season is not None else _season_from(match.group())
        stops["season"] = match.start()
    elif match := _SEASON_ROMAN.search(region):
        segment.season = segment.season if segment.season is not None else _ROMAN_SEASON[match["roman"].upper()]
        stops["season"] = match.start()
    part_at = None
    if match := _PART.search(region):
        segment.part = _number(match["n"])
        stops["part"] = part_at = match.start()
    if match := _VOLUME.search(region):
        stops["volume"] = match.start()
    if (match := _YEAR.search(region)) and region[: match.start()].strip():
        segment.year = int(match["year"])
        stops["year"] = match.start()
    pack = _PACK.search(text)
    named, named_region = (text[: pack.start()], region[: pack.start()]) if pack else (text, region)
    if match := _format_word(_FORMAT.search(named), named):
        segment.media_format = _format_of(match["format"])
        if match.start() < len(region):
            stops["format"] = match.start()
    elif match := _format_word(_SPECIAL.search(named_region), named_region):
        segment.media_format = _format_of(match["format"])
        stops["format"] = match.start()
    if (
        movie
        and not segment.media_format
        and (marker is None or marker.episode is None or marker.start >= movie.start())
    ):
        segment.media_format = "MOVIE"

    if not segment.marked and not folder and (title_end := _fall_back(segment, text, region, match)) is not None:
        stops["fallback episode"] = title_end
        # "Show 01 - Hitagi Crab, Part 1": a part after the episode belongs to the episode's title
        if part_at is not None and part_at > title_end:
            segment.part = None
            del stops["part"]

    cut = max(start, min(stops.values()))
    log.debug("    title stops: %s -> cut at %d", stops, cut)
    first = region.count(_GAP, 0, start)
    words = "".join(
        f" {groups[first + region.count(_GAP, start, start + i)]} "
        if char == _GAP and first + region.count(_GAP, start, start + i) in kept
        else char
        for i, char in enumerate(region[start:cut])
    )
    segment.marks = m[1] if (m := _TITLE_MARKS.search(words)) else ""
    segment.title = _clean_title(words)
    if tail := _FORMAT_TAIL.search(segment.title):
        segment.title = _TRAILING.sub("", segment.title[: tail.start()])
        segment.media_format = segment.media_format or ("TV" if tail["tv"] else "MOVIE")
    if not folder and (extra_title := _EXTRA_TITLE.search(segment.title)):
        segment.extra, segment.title = True, segment.title[: extra_title.start()].strip()
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
    if (
        not segment.title
        and marker
        and marker.text.lower().startswith(("episode", "episódio", "épisode"))
        and (rest := text[marker.end :].lstrip())
        and not rest.startswith(("-", "~", ":", _GAP))
    ):
        segment.episode_title = _clean_title(rest.split(_GAP, maxsplit=1)[0])
    for group in groups:
        _read_group(segment, group.strip())
    return segment


def _trailing_value(match: re.Match[str]) -> int:
    """The episode of a trailing number: the last one of "13-15" or "493-498 & 500-507"."""
    if numbers := re.findall(r"\d+", match["more"]):
        return int(numbers[-1])
    return int(match["last"] or match["ep"])


def _trailing_episode(region: str, groups: list[str]) -> re.Match[str] | None:
    """A number ending the title.

    Without a leading zero it needs a title before it, and not "Movie 26"; a single digit also needs a checksum
    after it ("Eve no Jikan 2 [88F4F7F0]"), and a year or resolution is never an episode.
    """
    match = _PADDED_EPISODE.search(region)
    if not match or match["ep"].startswith("0"):
        return match
    before = region[: match.start()]
    if not before.strip(_LEADING) or _NUMBERED_WORD.search(before) or _bare_episode(match) is None:
        return None
    if match["more"] or (match["last"] and int(match["last"]) > int(match["ep"])):
        return match
    if len(match["ep"]) > 1:
        return match
    following = region.count(_GAP, 0, match.start())
    return match if following < len(groups) and _CRC.fullmatch(groups[following].strip()) else None


def _fall_back(segment: _Segment, text: str, region: str, format_match: re.Match[str] | None) -> int | None:
    """Find an episode in a name without a marker.

    Returns:
        int | None: Where the title must end when the episode was part of the text, else None.
    """
    end = None
    if format_match and format_match["n"]:
        segment.episode = int(format_match["last"] or format_match["n"])
    elif match := next((m for g in segment.groups if (m := _GROUP_SEASON_EPISODE.fullmatch(g.strip()))), None):
        segment.season = segment.season if segment.season is not None else int(match["season"] or match["other"])
        segment.episode = int(match["ep"])
        number = _trailing_episode(region, segment.groups)
        end = number.start() if number else None
    elif match := _trailing_episode(region, segment.groups):
        segment.episode, end = _trailing_value(match), match.start()
    elif (episode := _group_episode(segment.groups)) is not None:
        segment.episode = episode
    elif (match := _AFTER_GAP.search(text)) and (episode := _bare_episode(match)) is not None:
        segment.episode, end = episode, match.start()
    elif (match := _TITLE_NUMBER_DASH.search(region)) and not _NUMBERED_WORD.search(region[: match.start()]):
        segment.episode, end = int(match["ep"]), match.start()
    elif match := _LEADING_EPISODE.match(region) or _LONE_NUMBER.fullmatch(region.replace(_GAP, " ").strip()):
        # The name starts with the episode, so it has no title of its own
        segment.episode, end = int(match["ep"]), 0
        if match.re is _LEADING_EPISODE:
            segment.episode_title = _clean_title(region[match.end() :].split(_GAP, maxsplit=1)[0])
    elif (match := _QUOTED_EPISODE.search(region)) and not _NUMBERED_WORD.search(region[: match.start()]):
        segment.episode, end = int(match["ep"]), match.start()
        segment.episode_title = _clean_title(match["title"])
    elif (match := _PADDED_WORDS.search(region)) and not _NUMBERED_WORD.search(region[: match.start()]):
        segment.episode, end = int(match["ep"]), match.start()
    return end


def _read_group(segment: _Segment, group: str) -> None:
    if match := _GROUP_SEASON.fullmatch(group) or _GROUP_SEASONS.match(group):
        segment.season = segment.season if segment.season is not None else int(match["season"])
        if match.re is _GROUP_SEASON and match["part"] and segment.part is None:
            segment.part = int(match["part"])
    elif (match := _GROUP_YEAR.fullmatch(group)) and segment.year is None:
        segment.year = int(match["year"])
    elif (match := _GROUP_FORMAT.fullmatch(group)) and segment.media_format is None:
        segment.media_format = _format_of(match["format"])
        if match["n"] and not segment.marked and segment.episode is None:
            segment.episode = int(match["n"])
    elif (match := _BRACKETED_EPISODE.fullmatch(group)) and match["absolute"]:
        segment.absolute = int(match["absolute"])
    # "S02E12 (063)": a bare number after a season's episode is the absolute one
    elif segment.marked and segment.season and re.fullmatch(r"\d{2,4}", group) and segment.absolute is None:
        value = int(group)
        if value > (segment.episode or 0) and value not in _RESOLUTIONS and not _year_like(value):
            segment.absolute = value


def _title_from_groups(groups: list[str]) -> str:
    """Names made only of brackets ("[Group][国漫][Title][2025]...") keep the title in one of them."""
    for group in groups:
        kind = next(kind for kind, found in _classify([group]).items() if found)
        words = _clean_title(group)
        # "3.0" looks like audio channels, so a long group of words is a title all the same
        if (
            (kind == "titles" or (kind == "tech" and len(words.split()) > 3))
            and len(_LATIN.findall(words)) >= 3
            and not (_TECH.search(words) or _TAG.fullmatch(words) or _ENCODED.search(words))
        ):
            return words
    return ""


def _strip_season(title: str) -> tuple[str, int | None, int | None]:
    """Take "S2" and "Part 2" out of a title found in a bracket, where `_analyse` never looked."""
    season = part = None
    if match := _SEASON_ORDINAL.search(title) or _SEASON.search(title):
        season, title = _last_season(match), title[: match.start()] + title[match.end() :]
    elif match := _SEASON_ROMAN.search(title):
        season, title = _ROMAN_SEASON[match["roman"].upper()], title[: match.start()] + title[match.end() :]
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
    # "01. Season 1 + Special", "02. OVA": the index orders the folders and is no title
    text = _prepare(_INDEXED.sub("", name) if folder else name)
    if folder and _MOVIE_FOLDER.fullmatch(text.strip()):
        return Release(media_format="MOVIE")
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
    # "[Mobile Suit Gundam Seed Destiny HD REMASTER][07][720p]": all brackets, and the first is the show
    if not title and episode is not None and release_group and len(release_group.split()) > 2:
        title = _clean_title(release_group)
    # Without an episode the name describes a pack or a movie, and any alternative may carry the details
    pool = used if any(s.marked for s in segments) else segments
    season = next((s.season for s in pool if s.season is not None), grouped_season)
    part = next((s.part for s in pool if s.part is not None), grouped_part)
    year = next((s.year for s in pool if s.year is not None), None)
    absolute = next((s.absolute for s in used if s.absolute is not None), None)
    # A format can sit in a trailing alternative: "... | Title | Other title (S02E00) (OVA 2)"
    media_format = next((s.media_format for s in pool if s.media_format), None) or next(
        (_format_of(m["format"]) for group in groups if (m := _GROUP_FORMAT.fullmatch(group.strip()))), None
    )

    skip = None
    if folder:
        skip = "an extra, not an episode" if _EXTRA_FOLDER.fullmatch(text.strip()) else None
    elif decimal and not media_format:
        skip = "a special between episodes"
    elif any(segment.extra for segment in used):
        skip = "an extra, not an episode"
    elif season == 0 and media_format is None:
        skip = "a season 0 special"
    elif episode == 0 and media_format is None:  # AniList has no episode 0; "Movie 00" and "OVA 00" name a format
        skip = "a prologue, episode 0"
    body = text[text.find("]") + 1 :] if release_group and text.lstrip().startswith("[") else text
    release = Release(
        title or None,
        episode,
        season,
        part,
        year,
        media_format,
        absolute,
        skip,
        group=release_group,
        crc=kinds["crc"][0] if kinds["crc"] else None,
        version=used[marked].version if used[marked].marked else None,
        resolution=m.group() if (m := _RESOLUTION.search(body)) else None,
        source=m.group() if (m := _SOURCE.search(body)) else None,
        tags=tuple(kinds["tags"]),
        alt_titles=tuple(kinds["titles"]),
        episode_title=next((s.episode_title for s in used if s.episode_title), None),
        marks=next((s.marks for s in used if s.title), ""),
    )
    log.debug("  result: %s", release)
    return release
