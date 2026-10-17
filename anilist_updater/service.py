"""The three actions mpv can ask for: detect the anime, record an episode, correct a detection."""

import logging
from dataclasses import replace

from .api import AniListClient
from .cache import Cache
from .errors import SkipUpdate
from .models import AnimeInfo, Correction, Options, Outcome, Status
from .parsing import parse_path
from .resolve import refresh, resolve
from .rules import Save, plan_update

log = logging.getLogger(__name__)


def _update_message(before: AnimeInfo, after: AnimeInfo, saves: list[Save]) -> str:
    name = before.anime_name or before.guessed_name
    if before.current_progress is None and before.current_status is None:
        return f'Added "{name}" to your list with progress: {after.current_progress}'
    if len(saves) > 1:
        return f'Updated "{name}" to {after.current_status} with progress: {after.current_progress}'
    return f'Updated "{name}" to: {after.current_progress}'


class Service:
    """Actions over an AniList client, a cache and the user's options."""

    def __init__(self, options: Options, client: AniListClient, cache: Cache) -> None:
        """Bind the collaborators."""
        self.options = options
        self.client = client
        self.cache = cache

    def info(self, path: str) -> Outcome:
        """Detect the anime and episode for a file, from the cache when it knows the directory."""
        file = parse_path(path)
        entry = self.cache.get(path, file.title)
        cached = entry.info_for(file.episode) if entry else None
        if cached:
            log.info('Using cached data for "%s": %s', file.title, cached.anime_id)

        info = cached or resolve(self.client, file)

        # An unfinished season has no episode count at first; corrected and sliding entries fill it in later.
        stale = cached and (entry.corrected or self.cache.sliding) and info.total_episodes is None
        if stale:
            info = refresh(self.client, info)

        # A cache hit isn't stored again, or its TTL would never run out.
        if (not cached or stale) and info.current_progress is not None:
            self.cache.put(path, info)
        self.cache.save()
        return Outcome(info)

    def update(self, path: str, info: AnimeInfo) -> Outcome:
        """Record the info's episode on AniList according to the user's options."""
        info = refresh(self.client, info)
        saves = plan_update(info, self.options)
        for save in saves:
            saved = self.client.save_entry(info.anime_id, save.status, save.progress)

        updated = replace(info, current_progress=saved["progress"], current_status=Status(saved["status"]))
        self.cache.put(path, updated)
        self.cache.save()
        return Outcome(updated, _update_message(info, updated, saves))

    def correct(self, path: str, info: AnimeInfo | None, correction: Correction) -> Outcome:
        """Apply the user's fixes to a detection: the AniList entry, the episode mapping and the status.

        Raises:
            SkipUpdate: If the correction changes nothing.
        """
        if info is None:
            file = parse_path(path)
            info = AnimeInfo(episode=file.episode, guessed_name=file.title, absolute_episode=file.episode)

        changes = []
        id_changed = info.anime_id != correction.anime_id
        if id_changed:
            changes.append(f"ID: {info.anime_id or '?'}->{correction.anime_id}")
            info = refresh(self.client, info, correction.anime_id)

        current_episode = info.episode or info.absolute_episode
        episode = max(1, correction.episode or current_episode)
        episode_changed = episode != current_episode
        if episode_changed:
            changes.append(f"Mapped {info.absolute_episode}->{episode}")

        if correction.status and correction.status != info.current_status:
            changes.append(f"Status: {info.current_status or 'None'}->{correction.status}")
            self.client.save_entry(correction.anime_id, correction.status)
            info = replace(info, current_status=correction.status)

        if not changes:
            raise SkipUpdate("No correction changes detected.")

        info = replace(info, episode=episode)
        self.cache.put(path, info, corrected=id_changed or episode_changed)
        self.cache.save()
        return Outcome(info, f'Corrected "{info.anime_name}" (ID: {info.anime_id}) | ' + " | ".join(changes))
