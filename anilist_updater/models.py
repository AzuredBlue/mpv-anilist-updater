"""Plain data shared by the parsing, resolution, rules and cache layers."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from enum import StrEnum
from typing import Any, Self


class Status(StrEnum):
    """AniList `MediaListStatus`."""

    CURRENT = "CURRENT"
    PLANNING = "PLANNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    REPEATING = "REPEATING"
    DROPPED = "DROPPED"


def _known(cls: type, data: Mapping[str, Any]) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in names}


@dataclass(frozen=True, slots=True)
class FileInfo:
    """What the path says about the media; `folder_season` says the season and part came from a folder."""

    title: str
    episode: int
    season: int | None = None
    part: int | None = None
    year: int | None = None
    media_format: str | None = None
    folder_season: bool = False
    absolute: int | None = None
    alt_title: str | None = None  # another name the file gives, to search when the title finds nothing
    # the parent folder's title when it extends the file's ("Initial D Fifth Stage")
    folder_title: str | None = None
    marks: str = ""  # the "!" or "!!" the title ends with, to tell "K-ON!" from "K-ON!!"
    arc: str | None = None  # the arc a season folder names ("Season 02 (Part 03) - Stardust Crusaders")
    # a season folder of the show was left out, as the file names more ("Show - Finale 1")
    unused_season: bool = False
    untitled: bool = False  # the filename names no title ("E12 - Welcome"); it comes from a folder

    @property
    def query(self) -> str:
        """The title as AniList names it: with the season and part, when they tell it apart."""
        words = [self.title]
        # AniList names the first part of a season like the whole season, but "Kizumonogatari Part 1" keeps its part
        part = self.part if self.part and (self.part > 1 or self.season is None) else None
        if self.season is not None and (self.season > 1 or part):
            words.append(f"Season {self.season}")
        if part:
            words.append(f"Part {part}")
        return " ".join(words)


@dataclass(frozen=True, slots=True)
class AnimeInfo:
    """A detected AniList entry and the user's progress on it.

    `episode` is relative to the entry; `absolute_episode` is the number in the filename.
    """

    anime_id: int | None = None
    anime_name: str | None = None
    episode: int | None = None
    current_progress: int | None = None
    total_episodes: int | None = None
    current_status: Status | None = None
    guessed_name: str = ""
    absolute_episode: int = 1
    mal_id: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> Self | None:
        """Build from request JSON; a missing key, null, `[]` and `{}` all mean absent."""
        if not data:
            return None
        values = _known(cls, data)
        if values.get("current_status"):
            values["current_status"] = Status(values["current_status"])
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the response."""
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Correction:
    """What the user asked for in the correction overlay."""

    anime_id: int
    episode: int | None = None
    status: Status | None = None
    score: float | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Build from request JSON; the score is in the user's AniList score format, and 0 clears it."""
        status = data.get("status")
        return cls(
            int(data["anime_id"]),
            data.get("episode") or None,
            Status(status.upper()) if status else None,
            data.get("score"),
        )


@dataclass(frozen=True, slots=True)
class Options:
    """User options. Lua owns the defaults and always sends every key; these only serve manual runs."""

    set_completed_to_rewatching_on_first_episode: bool = False
    update_progress_when_rewatching: bool = True
    set_to_completed_after_last_episode_current: bool = True
    set_to_completed_after_last_episode_rewatching: bool = True
    add_entry_if_missing: bool = False
    cache_refresh_rate: float = 24
    cache_mode: str = "NORMAL"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> Self:
        """Build from request JSON, whose keys are upper case."""
        return cls(**_known(cls, {k.lower(): v for k, v in (data or {}).items()}))

    @property
    def sliding(self) -> bool:
        """Whether every cache entry, not just corrected ones, slides on read."""
        return self.cache_mode.upper() == "SLIDING"


@dataclass(frozen=True, slots=True)
class Outcome:
    """Result of an action: the new info for Lua, an optional message for the user, search results or a score."""

    info: AnimeInfo | None = None
    message: str | None = None
    results: list[dict[str, Any]] | None = None
    score: dict[str, Any] | None = None
