import pytest

from anilist_updater.errors import SkipUpdate
from anilist_updater.models import AnimeInfo, Options, Status
from anilist_updater.rules import Save, plan_update


def info(status=Status.CURRENT, progress=3, episode=4, total=12):
    return AnimeInfo(
        anime_id=1, episode=episode, current_progress=progress, total_episodes=total, current_status=status
    )


def test_next_episode_is_recorded():
    assert plan_update(info(), Options()) == [Save(Status.CURRENT, 4)]


def test_planning_becomes_current():
    assert plan_update(info(Status.PLANNING, progress=0, episode=1), Options()) == [Save(Status.CURRENT, 1)]


@pytest.mark.parametrize("episode", [2, 3])
def test_old_episode_is_skipped(episode):
    with pytest.raises(SkipUpdate, match="not new"):
        plan_update(info(episode=episode), Options())


@pytest.mark.parametrize("status", [Status.COMPLETED, Status.DROPPED])
def test_unmodifiable_status_is_skipped(status):
    with pytest.raises(SkipUpdate, match="modifiable"):
        plan_update(info(status), Options())


def test_episode_zero_is_skipped():
    with pytest.raises(SkipUpdate, match="Episode 0"):
        plan_update(info(episode=0), Options())


@pytest.mark.parametrize(("flag", "completes"), [(True, True), (False, False)])
def test_last_episode_completes_when_enabled(flag, completes):
    options = Options(set_to_completed_after_last_episode_current=flag)
    expected = Status.COMPLETED if completes else Status.CURRENT
    assert plan_update(info(progress=11, episode=12), options) == [Save(expected, 12)]


def test_rewatching_keeps_status_and_ignores_progress_check():
    assert plan_update(info(Status.REPEATING, progress=10, episode=2), Options()) == [Save(Status.REPEATING, 2)]


def test_rewatching_disabled_skips():
    with pytest.raises(SkipUpdate):
        plan_update(info(Status.REPEATING), Options(update_progress_when_rewatching=False))


def test_completed_first_episode_rewatch_takes_two_writes():
    options = Options(set_completed_to_rewatching_on_first_episode=True)
    planned = plan_update(info(Status.COMPLETED, progress=12, episode=1), options)
    assert planned == [Save(Status.REPEATING, 0), Save(None, 1)]


def test_missing_entry_needs_opt_in():
    missing = info(None, progress=None, episode=1)
    with pytest.raises(SkipUpdate, match="on your list"):
        plan_update(missing, Options())
    assert plan_update(missing, Options(add_entry_if_missing=True)) == [Save(Status.CURRENT, 1)]


def test_missing_single_episode_entry_is_added_completed():
    only = info(None, progress=None, episode=1, total=1)
    assert plan_update(only, Options(add_entry_if_missing=True)) == [Save(Status.COMPLETED, 1)]
