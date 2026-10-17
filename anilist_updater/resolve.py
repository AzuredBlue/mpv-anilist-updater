"""Find the AniList entry for a parsed file, including absolute episode numbers across seasons."""

import logging
from dataclasses import replace
from typing import Any

from .api import AniListClient, media_title
from .errors import SkipUpdate
from .models import AnimeInfo, FileInfo, Status

log = logging.getLogger(__name__)

Media = dict[str, Any]

DEFAULT_FORMATS = ["TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA"]
MAIN_FORMATS = {"TV", "ONA"}
# Shorter than this is a short, not a main season.
MIN_DURATION = 21
DEFAULT_SEASON_LENGTH = 12


def _is_series_start(media: Media) -> bool:
    duration = media.get("duration")
    return media.get("format") in MAIN_FORMATS and (
        duration > MIN_DURATION if duration is not None else media.get("status") == "RELEASING"
    )


def main_series(candidates: list[Media]) -> list[Media] | None:
    """Order the main seasons by following SEQUEL relations from the first main TV/ONA.

    Args:
        candidates (list[Media]): Search results; a sequel missing from them ends the chain.

    Returns:
        list[Media] | None: The seasons in order, or None if there is no main series among them.
    """
    by_id = {m["id"]: m for m in candidates}
    node = next((m for m in candidates if _is_series_start(m)), None)
    if node is None:
        return None

    series = [node]
    visited = {node["id"]}
    while True:
        sequels = [
            edge["node"]
            for edge in node.get("relations", {}).get("edges", [])
            if edge["relationType"] == "SEQUEL" and edge["node"]["id"] in by_id
        ]
        if not sequels:
            break

        # Prefer a real season over a movie or special that also continues the story.
        sequel = max(sequels, key=lambda n: n["format"] in MAIN_FORMATS)
        if sequel["id"] in visited:
            break
        visited.add(sequel["id"])

        node = by_id[sequel["id"]]
        if node["format"] in MAIN_FORMATS:
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
        guessed_name=file.title,
        absolute_episode=file.episode,
    )


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
    formats = [file.media_format] if file.media_format else DEFAULT_FORMATS
    user_list, everything = client.search(file.title, file.year, formats)
    if not (user_list or everything):
        raise SkipUpdate(f"Couldn't find an anime from this title! ({file.title}). Is it in your list?")

    results = user_list or everything
    first = results[0]
    if not (first.get("episodes") and file.episode > first["episodes"]):
        info = _to_info(first, file.episode, file)
        log.info("Final guessed anime: %s", info.anime_name)
        return info

    # The episode exceeds the season, so it is probably absolute. The user's list may lack some seasons.
    pools = [results] if results is everything else [results, everything]
    for pool in pools:
        if located := locate_episode(main_series(pool), file.episode):
            season, relative = located
            info = _to_info(season, relative, file)
            log.info("Final guessed anime: %s", info.anime_name)
            log.info("Absolute episode %s corresponds to episode: %s", file.episode, relative)
            return info
    raise SkipUpdate(f"No valid seasons found for '{file.title}'.")


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
