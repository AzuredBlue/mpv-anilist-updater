"""Turn a media path into a title, episode, year and format, using guessit plus fixes for what it gets wrong."""

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import cache
from typing import Any

from .errors import ParseError
from .models import FileInfo

log = logging.getLogger(__name__)

GUESSIT_OPTIONS = {"excludes": ["country", "language"]}
FORMATS = {"Original Animated Video": "OVA", "Original Net Animation": "ONA"}

_NOISE = re.escape(r'\/:!*?"<>|._-')
# Separators are noise to guessit unless they sit before a number ("Title - 02", "Re:Zero 2").
_CLEAN = re.compile(rf"(?: - Movie)|[{_NOISE}](?!\s*\d)")
# "E05v2" would otherwise be read as an episode range.
_VERSION = re.compile(r"(E\d+)v\d")
# "Season 2 - 02", "S2 02" -> "S2 E02", or guessit takes it for a season range or mangles numeric titles.
_SEASON_EPISODE = re.compile(r"\b(?:season\s*|s)(\d+)\s*[-_ ]\s*(\d{1,4})\b", re.IGNORECASE)


@cache
def _guessit() -> Callable[..., Mapping[str, Any]]:
    # guessit takes ~150ms to import and only parsing needs it.
    from guessit import guessit  # ruff: ignore[import-outside-top-level]

    return guessit


def _guess(name: str) -> Mapping[str, Any]:
    return _guessit()(name, GUESSIT_OPTIONS)


def _normalise(name: str) -> str:
    name = " ".join(_CLEAN.sub(" ", name).split())
    name = _VERSION.sub(r"\1", name)
    return _SEASON_EPISODE.sub(r"S\1 E\2", name)


def _first(value: int | list[int] | None) -> int | None:
    return value[0] if isinstance(value, list) else value


@dataclass(slots=True)
class _Details:
    """Season, part and year, filled from the filename first and the folders after."""

    season: int | None = None
    part: str = ""
    year: int | None = None

    def fill_from(self, guess: Mapping[str, Any]) -> None:
        if self.season is None:
            self.season = _first(guess.get("season"))
        self.part = self.part or str(guess.get("part", ""))
        self.year = self.year or guess.get("year")


def _episode_and_season(guess: Mapping[str, Any]) -> tuple[int, int | None, list[int]]:
    """Pick the episode and season out of guessit's answer, plus any extra episodes it found."""
    episode = guess.get("episode")
    season = guess.get("season")

    # "S2 02": guessit calls the 02 an episode title
    episode_title = guess.get("episode_title", "")
    if "episode" not in guess and isinstance(episode_title, str) and episode_title.isdigit():
        episode = int(episode_title)

    # "[86, 13]" for EIGHTY-SIX, "[1, 2, 3]" for Ranma: the last number is the real episode
    remaining: list[int] = []
    if isinstance(episode, list):
        *remaining, episode = episode

    # "S2 03" gives season [2, 3]
    if isinstance(season, list):
        if episode is None and len(season) > 1:
            episode = season[-1]
        season = season[0]

    return (1 if episode is None else episode), season, remaining


def _title_in_filename(guess: Mapping[str, Any]) -> bool:
    """Whether the title comes before the season and episode.

    Releases are formatted "[Group] Title - S01E02". An episode first ("E12 - Welcome") means
    the filename has no title and the one guessit found is the episode's.
    """
    keys = list(guess)
    episode_index = keys.index("episode") if "episode" in guess else 1
    season_index = keys.index("season") if "season" in guess else -1
    return "title" in guess and episode_index > 0 and season_index != 0


def _title_from_folders(parts: list[str], details: _Details) -> str:
    """Look for the title in the parent folder, then the grandparent."""
    for folder in reversed(parts[:-1][-2:]):
        guess = _guess(folder)
        log.info("Folder guess: %s -> %s", folder, dict(guess))

        title = str(guess.get("title", ""))
        keys = list(guess)
        episode = guess.get("episode")
        # Numeric titles like "86" get parsed as an episode
        if isinstance(episode, int) and (not title or keys.index("episode") < keys.index("title")):
            title = f"{episode} {title}".strip()

        details.fill_from(guess)
        if title:
            return title
    return ""


def _media_format(guess: Mapping[str, Any]) -> str | None:
    others = guess.get("other", [])
    return next((FORMATS[o] for o in (others if isinstance(others, list) else [others]) if o in FORMATS), None)


def _compose_title(title: str, remaining: list[int], details: _Details, guess: Mapping[str, Any]) -> str:
    """Append what AniList titles carry: leftover numbers, season and part."""
    words = [title, *map(str, remaining)]
    keys = list(guess)

    # "(2019)" is read as both year and season
    season = None if details.season == details.year else details.season
    if season is not None and (season > 1 or details.part):
        words.append(f"Season {season}")

    # "Encounter, Part 2" as an episode title is not a part of the show
    episode_title_index = keys.index("episode_title") if "episode_title" in keys else len(keys)
    if details.part and ("part" not in keys or keys.index("part") < episode_title_index):
        words.append(f"Part {details.part}")

    return " ".join(words)


def parse_path(path: str) -> FileInfo:
    """Extract title, episode, year and format from a media path.

    Args:
        path (str): Path to the video, or the media title for streams.

    Returns:
        FileInfo: What the path says about the media.

    Raises:
        ParseError: If neither the filename nor its folders contain a title.
    """
    parts = path.replace("\\", "/").split("/")
    filename = _normalise(parts[-1])
    guess = _guess(filename)
    log.info("File name guess: %s -> %s", filename, dict(guess))

    episode, season, remaining = _episode_and_season(guess)
    details = _Details(season, str(guess.get("part", "")), guess.get("year"))

    title = str(guess["title"]) if _title_in_filename(guess) else _title_from_folders(parts, details)
    if not title:
        raise ParseError(f"Couldn't find title in filename '{filename}'! Guess result: {dict(guess)}")

    info = FileInfo(_compose_title(title, remaining, details, guess), episode, details.year, _media_format(guess))
    log.info("Guessed: %s", info)
    return info
