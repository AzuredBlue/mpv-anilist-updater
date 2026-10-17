import pytest
from test_resolve import FakeClient, media

from anilist_updater.cache import Cache
from anilist_updater.errors import SkipUpdate
from anilist_updater.models import AnimeInfo, Correction, Options, Status
from anilist_updater.service import Service

PATH = "/anime/[Group] Show - 05 [1080p].mkv"
ENTRY = {"status": "CURRENT", "progress": 4}


class RecordingClient(FakeClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.saves, self.searches = [], 0

    def search(self, title, year, formats):
        self.searches += 1
        return super().search(title, year, formats)

    def save_entry(self, anime_id, status=None, progress=None):
        self.saves.append((anime_id, status, progress))
        return {"status": status or "CURRENT", "progress": progress}


def make(tmp_path, client, **options):
    opts = Options(**options)
    return Service(opts, client, Cache(opts, tmp_path / "cache.json"))


def test_info_then_cache_hit_skips_the_search(tmp_path):
    client = RecordingClient(user=[media(1, 12, entry=ENTRY)])
    first = make(tmp_path, client).info(PATH).info
    assert (first.anime_id, first.episode, first.current_progress) == (1, 5, 4)

    second = make(tmp_path, client).info(PATH).info
    assert client.searches == 1
    assert second == first


def test_info_without_list_entry_is_not_cached(tmp_path):
    client = RecordingClient(everything=[media(1, 12)])
    make(tmp_path, client).info(PATH)
    make(tmp_path, client).info(PATH)
    assert client.searches == 2


def test_update_records_progress_and_caches(tmp_path):
    client = RecordingClient(user=[media(1, 12, entry=ENTRY)])
    service = make(tmp_path, client)
    outcome = service.update(PATH, service.info(PATH).info)

    assert client.saves == [(1, Status.CURRENT, 5)]
    assert outcome.message == 'Updated "Show 1" to: 5'
    assert outcome.info.current_progress == 5
    assert make(tmp_path, client).cache.get(PATH, "Show").current_progress == 5


def test_update_skip_reason_propagates(tmp_path):
    client = RecordingClient()
    info = AnimeInfo(anime_id=1, episode=3, current_progress=3, current_status=Status.CURRENT)
    with pytest.raises(SkipUpdate, match="not new"):
        make(tmp_path, client).update(PATH, info)
    assert client.saves == []


def test_update_adds_missing_entry_when_enabled(tmp_path):
    client = RecordingClient()
    info = AnimeInfo(anime_id=1, anime_name="Show 1", episode=1, total_episodes=12)
    outcome = make(tmp_path, client, add_entry_if_missing=True).update(PATH, info)
    assert outcome.message == 'Added "Show 1" to your list with progress: 1'


def test_correct_changes_id_episode_and_status(tmp_path):
    client = RecordingClient(user=[media(1, 12, entry=ENTRY)], by_id={2: media(2, 24)})
    service = make(tmp_path, client)
    detected = service.info(PATH).info

    outcome = service.correct(PATH, detected, Correction(2, episode=7, status=Status.PAUSED))
    assert client.saves == [(2, Status.PAUSED, None)]
    assert outcome.message == 'Corrected "Show 2" (ID: 2) | ID: 1->2 | Mapped 5->7 | Status: None->PAUSED'

    entry = make(tmp_path, client).cache.get(PATH, "Show")
    assert (entry.anime_id, entry.corrected, entry.info_for(5).episode) == (2, True, 7)


def test_correct_without_changes_is_skipped(tmp_path):
    client = RecordingClient(user=[media(1, 12, entry=ENTRY)])
    service = make(tmp_path, client)
    detected = service.info(PATH).info
    with pytest.raises(SkipUpdate, match="No correction"):
        service.correct(PATH, detected, Correction(1))


def test_correct_without_detection_parses_the_path(tmp_path):
    client = RecordingClient(by_id={3: media(3, 12)})
    outcome = make(tmp_path, client).correct(PATH, None, Correction(3))
    assert (outcome.info.anime_id, outcome.info.guessed_name, outcome.info.episode) == (3, "Show", 5)
