"""Per-directory cache of detected anime, so most files load without searching AniList."""

import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Self

from .models import AnimeInfo, Options, Status
from .release import fold_title

log = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).resolve().parent.parent / "cache.json"
QUEUE_PATH = Path(__file__).resolve().parent.parent / "queue.json"
CORRECTED_TTL = 28 * 24 * 60 * 60
LOCK_WAIT = 2
LOCK_STALE = 120


def _write_json(path: Path, payload: object) -> None:
    """Replace `path` with `payload` in one step, so an interrupted run can't leave half a file."""
    with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", delete=False, encoding="utf-8") as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2)
    Path(tmp.name).replace(path)


def _key(filepath: str, title: str) -> str:
    # Streams are named by title alone; in a folder, each title gets its own entry.
    directory = os.path.dirname(filepath)
    return f"{directory}|{fold_title(title)}" if directory else f"title:{title}"


def _legacy_key(filepath: str) -> str:
    return hashlib.sha256(os.path.dirname(filepath).encode()).hexdigest()


@dataclass(slots=True)
class Entry:
    """The anime a directory resolved to.

    A file's episode relative to the entry is `absolute - offset`: with offset 12, absolute 13 is episode 1.
    """

    guessed_name: str
    anime_id: int
    anime_name: str | None
    current_progress: int | None
    total_episodes: int | None
    current_status: Status | None
    offset: int
    corrected: bool
    ttl: float
    mal_id: int | None = None

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
            current_progress=raw.get("current_progress"),
            total_episodes=raw.get("total_episodes"),
            current_status=Status(raw["current_status"]) if raw.get("current_status") else None,
            offset=offset,
            corrected=bool(raw.get("corrected")),
            ttl=raw["ttl"],
            mal_id=raw.get("mal_id"),
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
            anime_name=self.anime_name or self.guessed_name,
            episode=relative,
            current_progress=self.current_progress,
            total_episodes=self.total_episodes,
            current_status=self.current_status,
            guessed_name=self.guessed_name,
            absolute_episode=absolute,
            mal_id=self.mal_id,
        )


class Cache:
    """Read lazily, written at most once and only if something changed.

    Saving merges just the changed keys into the file as it is then, so runs in parallel don't undo each other.
    """

    def __init__(self, options: Options, path: Path = CACHE_PATH, clock: Callable[[], float] = time.time) -> None:
        """Create a cache over `path`; `clock` is injectable for tests."""
        self.sliding = options.sliding
        self._ttl = float(options.cache_refresh_rate) * 3600
        self._path = path
        self._clock = clock
        self._changed: set[str] = set()
        self._dropped: set[str] = set()

    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @cached_property
    def _entries(self) -> dict[str, Entry]:
        now, entries = self._clock(), {}
        for key, value in self._read().items():
            try:
                entry = Entry.from_json(value)
            except (KeyError, ValueError, TypeError, AttributeError):
                self._dropped.add(key)
                continue
            if entry.ttl < now:
                self._dropped.add(key)
            else:
                entries[key] = entry
        return entries

    def _entry(self, filepath: str, title: str) -> Entry | None:
        """The title's entry in the file's directory, moving one stored under an older key to the current one."""
        key = _key(filepath, title)
        directory = os.path.dirname(filepath)
        if key not in self._entries and directory:
            # One entry per directory, stored by its path or by the hash of it
            for old in (directory, _legacy_key(filepath)):
                if (entry := self._entries.get(old)) and fold_title(entry.guessed_name) == fold_title(title):
                    self._entries[key] = self._entries.pop(old)
                    self._changed.add(key)
                    self._dropped.add(old)
                    break
        return self._entries.get(key)

    def get(self, filepath: str, guessed_name: str) -> Entry | None:
        """Look up the directory's entry for this title."""
        entry = self._entry(filepath, guessed_name)
        if entry is None:
            return None

        if self.sliding or entry.corrected:
            window = CORRECTED_TTL if entry.corrected else self._ttl
            now = self._clock()
            if entry.ttl <= now + window / 2:
                entry.ttl = now + window
                self._changed.add(_key(filepath, guessed_name))
        return entry

    def put(self, filepath: str, info: AnimeInfo, *, corrected: bool = False) -> None:
        """Store `info` for the file's directory; once corrected, an entry stays corrected."""
        if info.anime_id is None or info.episode is None:
            return
        previous = self._entry(filepath, info.guessed_name)
        corrected = corrected or bool(previous and previous.corrected and previous.anime_id == info.anime_id)

        key = _key(filepath, info.guessed_name)
        self._entries[key] = Entry(
            guessed_name=info.guessed_name,
            anime_id=info.anime_id,
            anime_name=info.anime_name,
            current_progress=info.current_progress,
            total_episodes=info.total_episodes,
            current_status=info.current_status,
            offset=info.absolute_episode - info.episode,
            corrected=corrected,
            ttl=self._clock() + (CORRECTED_TTL if corrected else self._ttl),
            mal_id=info.mal_id,
        )
        self._changed.add(key)

    def save(self) -> None:
        """Write the cache if it changed, atomically so an interrupted run can't corrupt it."""
        if not (self._changed or self._dropped):
            return
        payload = {key: value for key, value in self._read().items() if key not in self._dropped}
        payload.update({key: self._entries[key].to_json() for key in self._changed})
        try:
            _write_json(self._path, payload)
        except OSError as e:
            log.warning("Failed saving cache: %s", e)
        else:
            self._changed.clear()
            self._dropped.clear()


class Pending:
    """Updates that couldn't reach AniList, kept in `queue.json` until a later run can send them.

    The file exists only while something is pending. Every change re-reads it and is written atomically, so a
    run that is killed leaves the queue as it was before or after that change, never half of it.
    """

    def __init__(self, path: Path = QUEUE_PATH) -> None:
        """Create a queue over `path`."""
        self._path = path

    def _read(self) -> list[dict[str, Any]]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict) and isinstance(item.get("path"), str)]

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Hold `queue.lock` while a change is read and written, so two runs can't overwrite each other.

        The lock is a file created exclusively, which every OS does atomically. One left behind by a killed run
        is taken over after `LOCK_STALE` seconds; if it can't be had in `LOCK_WAIT` the change goes ahead
        anyway, as losing the update is worse than the small chance of a clash.
        """
        lock = self._path.with_suffix(".lock")
        deadline, held = time.monotonic() + LOCK_WAIT, False
        while not held and time.monotonic() < deadline:
            try:
                os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                held = True
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > LOCK_STALE:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
            except OSError:
                break
        try:
            yield
        finally:
            if held:
                lock.unlink(missing_ok=True)

    def _write(self, items: list[dict[str, Any]]) -> None:
        if items:
            _write_json(self._path, items)
        else:
            self._path.unlink(missing_ok=True)

    @staticmethod
    def _item(path: str, info: AnimeInfo) -> dict[str, Any]:
        return {"path": path, "info": info.to_dict()}

    def items(self) -> list[tuple[str, AnimeInfo]]:
        """The queued updates, oldest first; entries that can't be read are left out."""
        items = []
        for raw in self._read():
            try:
                info = AnimeInfo.from_dict(raw.get("info"))
            except (ValueError, TypeError, AttributeError):
                continue
            if info and info.anime_id is not None:
                items.append((raw["path"], info))
        return items

    def add(self, path: str, info: AnimeInfo) -> None:
        """Queue an update; the same one twice is kept once. A failed write raises, as the update would be lost."""
        item = json.loads(json.dumps(self._item(path, info)))
        with self._locked():
            items = self._read()
            if item not in items:
                self._write([*items, item])

    def remove(self, path: str, info: AnimeInfo) -> None:
        """Forget a queued update once it is done; a failed write only means it is tried again."""
        item = json.loads(json.dumps(self._item(path, info)))
        with self._locked():
            items = self._read()
            if item not in items:
                return
            items.remove(item)
            try:
                self._write(items)
            except OSError as e:
                log.warning("Failed saving the queue: %s", e)
