"""Decide which AniList writes a watched episode warrants. Pure, so every option combination is testable."""

from dataclasses import dataclass

from .errors import SkipUpdate
from .models import AnimeInfo, Options, Status

MODIFIABLE = {Status.CURRENT, Status.PLANNING, Status.PAUSED}


@dataclass(frozen=True, slots=True)
class Save:
    """One `SaveMediaListEntry` call; a None status leaves the status alone."""

    status: Status | None
    progress: int


def plan_update(info: AnimeInfo, options: Options) -> list[Save]:
    """Work out the writes for watching `info.episode`.

    Args:
        info (AnimeInfo): The detected entry and the user's current state.
        options (Options): User options.

    Returns:
        list[Save]: The writes to perform, in order.

    Raises:
        SkipUpdate: If the episode shouldn't be recorded, with the reason.
    """
    episode, status = info.episode, info.current_status
    if info.anime_id is None:
        raise SkipUpdate("Couldn't find that anime! Make sure it is on your list and the title is correct.")
    if not episode:
        raise SkipUpdate("Episode 0 is not counted on AniList. Not updating.")

    is_last = episode == info.total_episodes

    if info.current_progress is None and status is None:
        if not options.add_entry_if_missing:
            raise SkipUpdate("Failed to get current episode count. Is it on your list?")
        completed = is_last and options.set_to_completed_after_last_episode_current
        return [Save(Status.COMPLETED if completed else Status.CURRENT, episode)]

    if status == Status.COMPLETED and episode == 1 and options.set_completed_to_rewatching_on_first_episode:
        # AniList resets the progress when a completed entry changes to rewatching in the same
        # request that sets it, so the progress needs its own request.
        completes = is_last and options.set_to_completed_after_last_episode_rewatching
        return [Save(Status.REPEATING, 0), Save(Status.COMPLETED if completes else None, 1)]

    if status == Status.REPEATING and options.update_progress_when_rewatching:
        target = Status.REPEATING
    elif status in MODIFIABLE:
        target = Status.CURRENT
    else:
        raise SkipUpdate(f"Anime is not in a modifiable state (status: {status}). Not updating.")

    if info.current_progress is not None and episode <= info.current_progress:
        raise SkipUpdate(f"Episode was not new. Not updating ({episode} <= {info.current_progress})")

    if is_last and (
        (status in MODIFIABLE and options.set_to_completed_after_last_episode_current)
        or (status == Status.REPEATING and options.set_to_completed_after_last_episode_rewatching)
    ):
        target = Status.COMPLETED
    return [Save(target, episode)]
