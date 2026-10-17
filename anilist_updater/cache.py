"""Per-directory cache of detected anime, so most files load without searching AniList."""

import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Self

from .models import AnimeInfo, Options, Status

log = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parent.parent / "cache.json"
CORRECTED_TTL = 28 * 24 * 60 * 60


def _key(filepath: str) -> str:
    return os.path.dirname(filepath)


def _legacy_key(filepath: str) -> str:
    return hashlib.sha256(_key(filepath).encode()).hexdigest()


@dataclass(slots=True)
class Entry:
    """The anime a directory resolved to.

    A file's episode relative to the entry is `absolute - offset`: with offset 12, absolute 13 is episode 1.
    """

    guessed_name: str
    anime_id: int
    anime_name: str | None
    mal_id: int | None
    current_progress: int | None
    total_episodes: int | None
    current_status: Status | None
    offset: int
    corrected: bool
    ttl: float

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Self:
        """Read an entry, converting the old `"19->7"` mapping."""
        offset = raw.get("offset")
        if offset is None:
            absolute, _, relative = raw["relative_progress"].partition("->")
            offset = int(absolute) - int(relative)
        return cls(
            guessed_name=raw["guessed_name"],
            anime_id=raw["anime_id"],
            anime_name=raw.get("anime_name"),
            mal_id=raw.get("mal_id"),
            current_progress=raw.get("current_progress"),
            total_episodes=raw.get("total_episodes"),
            current_status=Status(raw["current_status"]) if raw.get("current_status") else None,
            offset=offset,
            corrected=bool(raw.get("corrected")),
            ttl=raw["ttl"],
        )

    def to_json(self) -> dict[str, Any]:
        """Serialise for the cache file."""
        return asdict(self)

    def info_for(self, absolute: int) -> AnimeInfo | None:
        """The cached info for a file's absolute episode, or None if it falls outside the entry."""
        relative = absolute - self.offset
        if not 1 <= relative <= (self.total_episodes or 999):
            return None
        return AnimeInfo(
            anime_id=self.anime_id,
            mal_id=self.mal_id,
            anime_name=self.anime_name or self.guessed_name,
            episode=relative,
            current_progress=self.current_progress,
            total_episodes=self.total_episodes,
            current_status=self.current_status,
            guessed_name=self.guessed_name,
            absolute_episode=absolute,
        )


class Cache:
    """Read lazily, written at most once and only if something changed."""

    def __init__(self, options: Options, path: Path = CACHE_PATH, clock: Callable[[], float] = time.time) -> None:
        """Create a cache over `path`; `clock` is injectable for tests."""
        self.sliding = options.sliding
        self._ttl = float(options.cache_refresh_rate) * 3600
        self._path = path
        self._clock = clock
        self._dirty = False

    @cached_property
    def _entries(self) -> dict[str, Entry]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

        now, entries = self._clock(), {}
        for key, value in raw.items():
            try:
                entry = Entry.from_json(value)
            except (KeyError, ValueError, TypeError, AttributeError):
                self._dirty = True
                continue
            if entry.ttl < now:
                self._dirty = True
            else:
                entries[key] = entry
        return entries

    def _entry(self, filepath: str) -> Entry | None:
        """The directory's entry, moving one stored under the old hashed key to its path."""
        key = _key(filepath)
        if key not in self._entries and (legacy := self._entries.pop(_legacy_key(filepath), None)):
            self._entries[key] = legacy
            self._dirty = True
        return self._entries.get(key)

    def get(self, filepath: str, guessed_name: str) -> Entry | None:
        """Look up the directory's entry if it was detected from the same title."""
        entry = self._entry(filepath)
        if entry is None or entry.guessed_name != guessed_name:
            return None

        if self.sliding or entry.corrected:
            window = CORRECTED_TTL if entry.corrected else self._ttl
            now = self._clock()
            if entry.ttl <= now + window / 2:
                entry.ttl = now + window
                self._dirty = True
        return entry

    def put(self, filepath: str, info: AnimeInfo, *, corrected: bool = False) -> None:
        """Store `info` for the file's directory; once corrected, an entry stays corrected."""
        if info.anime_id is None or info.episode is None:
            return
        previous = self._entry(filepath)
        corrected = corrected or bool(previous and previous.corrected)

        self._entries[_key(filepath)] = Entry(
            guessed_name=info.guessed_name,
            anime_id=info.anime_id,
            anime_name=info.anime_name,
            mal_id=info.mal_id,
            current_progress=info.current_progress,
            total_episodes=info.total_episodes,
            current_status=info.current_status,
            offset=info.absolute_episode - info.episode,
            corrected=corrected,
            ttl=self._clock() + (CORRECTED_TTL if corrected else self._ttl),
        )
        self._dirty = True

    def save(self) -> None:
        """Write the cache if it changed, atomically so an interrupted run can't corrupt it."""
        if not self._dirty:
            return
        payload = {key: entry.to_json() for key, entry in self._entries.items()}
        try:
            with tempfile.NamedTemporaryFile(
                "w", dir=self._path.parent, suffix=".tmp", delete=False, encoding="utf-8"
            ) as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
            Path(tmp.name).replace(self._path)
        except OSError as e:
            log.warning("Failed saving cache: %s", e)
        else:
            self._dirty = False
