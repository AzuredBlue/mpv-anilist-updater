import json
from dataclasses import replace

import pytest
from fakes import FakeClient, media

from anilist_updater import cli
from anilist_updater.api import AniListClient
from anilist_updater.cache import Cache, Pending
from anilist_updater.errors import AniListError, SkipUpdate
from anilist_updater.models import AnimeInfo, Correction, Options, Status
from anilist_updater.service import Service

PATH = "/anime/[Group] Show - 05 [1080p].mkv"
ENTRY = {"status": "CURRENT", "progress": 4}


class RecordingClient(FakeClient):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.saves, self.scores, self.searches = [], [], 0

    def search(self, title, year, formats):
        self.searches += 1
        return super().search(title, year, formats)

    def save_entry(self, anime_id, status=None, progress=None, score=None):
        self.saves.append((anime_id, status, progress))
        if score is not None:
            self.scores.append(score)
        return {"status": status or "CURRENT", "progress": progress}


def make(tmp_path, client, **options):
    opts = Options(**options)
    return Service(opts, client, Cache(opts, tmp_path / "cache.json"), Pending(tmp_path / "queue.json"))


@pytest.fixture
def detected(tmp_path):
    client = RecordingClient(user=[media(1, 12, entry=ENTRY)])
    service = make(tmp_path, client)
    return client, service, service.info(PATH).info


def test_info_then_cache_hit_skips_the_search(tmp_path):
    client = RecordingClient(user=[media(1, 12, entry=ENTRY)])
    first = make(tmp_path, client).info(PATH).info
    assert (first.anime_id, first.episode, first.current_progress) == (1, 5, 4)

    second = make(tmp_path, client).info(PATH).info
    assert client.searches == 1
    assert second == first


def test_info_response_carries_the_mal_id(monkeypatch, tmp_path):
    """Other programs read `info.mal_id` from the response, fresh and from the cache."""
    client = RecordingClient(user=[media(1, 12, mal=4242, entry=ENTRY)])
    monkeypatch.setattr(AniListClient, "from_token_file", lambda: client)
    monkeypatch.setattr(cli, "Cache", lambda options: Cache(options, tmp_path / "cache.json"))
    monkeypatch.setattr(cli, "Pending", lambda: Pending(tmp_path / "queue.json"))
    request = {"action": "info", "path": PATH, "options": {}}

    assert cli.handle(request)["info"]["mal_id"] == 4242
    assert cli.handle(request)["info"]["mal_id"] == 4242
    assert client.searches == 1


def test_info_response_has_a_null_mal_id_when_anilist_has_none(monkeypatch, tmp_path):
    client = RecordingClient(user=[media(1, 12, entry=ENTRY)])
    monkeypatch.setattr(AniListClient, "from_token_file", lambda: client)
    monkeypatch.setattr(cli, "Cache", lambda options: Cache(options, tmp_path / "cache.json"))
    monkeypatch.setattr(cli, "Pending", lambda: Pending(tmp_path / "queue.json"))

    response = cli.handle({"action": "info", "path": PATH, "options": {}})
    assert (response["ok"], response["info"]["mal_id"]) == (True, None)


def test_resolved_info_is_not_refreshed_again(tmp_path):
    client = RecordingClient(user=[media(2, None, entry=ENTRY)])
    service = make(tmp_path, client)
    known = AnimeInfo(
        anime_id=1, episode=1, total_episodes=3, current_progress=0, guessed_name="Show", absolute_episode=1
    )
    service.cache.put(PATH, known, corrected=True)
    assert service.info(PATH).info.anime_id == 2


def test_info_without_list_entry_is_not_cached(tmp_path):
    client = RecordingClient(everything=[media(1, 12)])
    make(tmp_path, client).info(PATH)
    make(tmp_path, client).info(PATH)
    assert client.searches == 2


def test_update_records_progress_and_caches(tmp_path):
    client = RecordingClient(user=[media(1, 12, entry=ENTRY)], by_id={1: media(1, 12, entry=ENTRY)})
    service = make(tmp_path, client)
    outcome = service.update(PATH, service.info(PATH).info)

    assert client.saves == [(1, Status.CURRENT, 5)]
    assert outcome.message == 'Updated "Show 1" to: 5'
    assert outcome.info.current_progress == 5
    assert make(tmp_path, client).cache.get(PATH, "Show").current_progress == 5


def test_update_skip_reason_propagates(tmp_path):
    client = RecordingClient(by_id={1: media(1, 12, entry={"status": "CURRENT", "progress": 3})})
    info = AnimeInfo(anime_id=1, episode=3, current_progress=3, current_status=Status.CURRENT)
    with pytest.raises(SkipUpdate, match="not new"):
        make(tmp_path, client).update(PATH, info)
    assert client.saves == []


def test_update_adds_missing_entry_when_enabled(tmp_path):
    client = RecordingClient(by_id={1: media(1, 12)})
    info = AnimeInfo(anime_id=1, anime_name="Show 1", episode=1, total_episodes=12)
    outcome = make(tmp_path, client, add_entry_if_missing=True).update(PATH, info)
    assert outcome.message == 'Added "Show 1" to your list with progress: 1'


def test_update_uses_the_current_list_state_not_the_cached_one(tmp_path):
    client = RecordingClient(by_id={1: media(1, 12, entry={"status": "COMPLETED", "progress": 12})})
    info = AnimeInfo(anime_id=1, episode=5, current_progress=4, current_status=Status.CURRENT)
    with pytest.raises(SkipUpdate, match="not in a modifiable state"):
        make(tmp_path, client).update(PATH, info)
    assert client.saves == []


def test_correct_changes_id_episode_and_status(tmp_path):
    client = RecordingClient(user=[media(1, 12, entry=ENTRY)], by_id={2: media(2, 24)})
    service = make(tmp_path, client)
    detected = service.info(PATH).info

    outcome = service.correct(PATH, detected, Correction(2, episode=7, status=Status.PAUSED))
    assert client.saves == [(2, Status.PAUSED, None)]
    assert outcome.message == 'Corrected "Show 2" (ID: 2) | ID: 1->2 | Mapped 5->7 | Status: None->PAUSED'

    entry = make(tmp_path, client).cache.get(PATH, "Show")
    assert (entry.anime_id, entry.corrected, entry.info_for(5).episode) == (2, True, 7)


def test_correct_score_alone_is_one_save_without_a_status(detected):
    client, service, info = detected
    outcome = service.correct(PATH, info, Correction(1, score=8.5))
    assert (client.saves, client.scores) == ([(1, None, None)], [8.5])
    assert outcome.message.endswith("| Score: 8.5")
    assert outcome.info.current_status == Status.CURRENT


def test_correct_status_and_score_share_a_save(detected):
    client, service, info = detected
    outcome = service.correct(PATH, info, Correction(1, status=Status.PAUSED, score=7))
    assert (client.saves, client.scores) == ([(1, Status.PAUSED, None)], [7])
    assert outcome.info.current_status == Status.PAUSED


def test_correct_score_zero_clears_it(detected):
    client, service, info = detected
    outcome = service.correct(PATH, info, Correction(1, score=0))
    assert client.scores == [0]
    assert outcome.message.endswith("| Score: cleared")


def test_correct_without_changes_is_skipped(detected):
    _, service, info = detected
    with pytest.raises(SkipUpdate, match="No correction"):
        service.correct(PATH, info, Correction(1))


def test_correct_without_detection_parses_the_path(tmp_path):
    client = RecordingClient(by_id={3: media(3, 12)})
    outcome = make(tmp_path, client).correct(PATH, None, Correction(3))
    assert (outcome.info.anime_id, outcome.info.guessed_name, outcome.info.episode) == (3, "Show", 5)


def test_update_does_not_re_add_a_removed_entry(tmp_path):
    client = RecordingClient(by_id={1: media(1, 12)})
    info = AnimeInfo(anime_id=1, episode=5, current_progress=4, current_status=Status.CURRENT, total_episodes=12)
    with pytest.raises(SkipUpdate, match="on your list"):
        make(tmp_path, client).update(PATH, info)
    assert client.saves == []


class Flaky(RecordingClient):
    """A client whose requests fail with `error` while `down`."""

    def __init__(self, error=None, **kwargs):
        super().__init__(**kwargs)
        self.down, self.error, self.requests = False, error or AniListError("maintenance", 503, retryable=True), 0

    def media_by_id(self, anime_id):
        self.requests += 1
        if self.down:
            raise self.error
        return super().media_by_id(anime_id)

    def save_entry(self, anime_id, status=None, progress=None, score=None):
        if self.down:
            raise self.error
        return super().save_entry(anime_id, status, progress, score)


def episode(number, anime_id=1, name="Show"):
    return AnimeInfo(
        anime_id=anime_id,
        anime_name=f"Show {anime_id}",
        episode=number,
        current_progress=number - 1,
        current_status=Status.CURRENT,
        total_episodes=12,
        guessed_name=name,
        absolute_episode=number,
    )


def queue_make(tmp_path, client, **options):
    opts = Options(**options)
    return Service(opts, client, Cache(opts, tmp_path / "cache.json"), Pending(tmp_path / "queue.json"))


@pytest.fixture
def offline(tmp_path):
    client = Flaky(
        by_id={1: media(1, 12, entry=ENTRY), 2: media(2, 12, entry={"status": "CURRENT", "progress": 1})}
    )
    service = queue_make(tmp_path, client)
    client.down = True
    return client, service, tmp_path / "queue.json"


def test_update_while_anilist_is_down_is_queued(offline):
    client, service, queue = offline
    outcome = service.update(PATH, episode(5))
    assert "will be sent later" in outcome.message
    assert client.saves == []
    assert json.loads(queue.read_text())[0]["info"]["episode"] == 5


def test_update_failure_that_retrying_cannot_fix_is_not_queued(tmp_path):
    client = Flaky(AniListError("bad", 400), by_id={1: media(1, 12, entry=ENTRY)})
    service = queue_make(tmp_path, client)
    client.down = True
    with pytest.raises(AniListError):
        service.update(PATH, episode(5))
    assert not (tmp_path / "queue.json").exists()


def test_detecting_a_new_file_sends_the_queue_once_anilist_answers(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    client.down = False
    client.user = [media(2, 12, entry={"status": "CURRENT", "progress": 1})]
    service.info("/other/Show 2 - 02.mkv")
    assert client.saves == [(1, Status.CURRENT, 5)]
    assert not queue.exists()


def test_detecting_a_cached_file_makes_no_request_and_keeps_the_queue(offline):
    client, service, queue = offline
    service.cache.put(PATH, episode(5))
    service.update(PATH, episode(5))
    client.down = False
    before = client.requests
    service.info(PATH)
    assert client.requests == before
    assert queue.exists()


def test_detecting_while_down_fails_without_touching_the_queue(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    client.user = [media(2, 12)]
    client.down = False
    client.search = lambda *_: (_ for _ in ()).throw(AniListError("maintenance", 503, retryable=True))
    with pytest.raises(AniListError):
        service.info("/other/Show 2 - 02.mkv")
    assert len(json.loads(queue.read_text())) == 1


def test_still_down_makes_one_request_and_keeps_everything(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    service.update(PATH, episode(6))
    before = client.requests
    service.drain()
    assert client.requests == before + 1
    assert [i["info"]["episode"] for i in json.loads(queue.read_text())] == [5, 6]


def test_update_while_still_down_makes_only_its_own_request_and_joins_the_queue(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    before = client.requests
    service.update(PATH, episode(6))
    assert client.requests == before + 1
    assert len(json.loads(queue.read_text())) == 2


def test_a_successful_update_sends_the_queue_of_other_anime(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    service.update(PATH, episode(6))
    client.down = False
    outcome = service.update("/other/Show 2 - 02.mkv", episode(2, anime_id=2, name="Other"))
    assert client.saves == [(2, Status.CURRENT, 2), (1, Status.CURRENT, 5), (1, Status.CURRENT, 6)]
    assert outcome.message.endswith("(+2 queued sent)")
    assert not queue.exists()


def test_queued_episodes_of_the_same_anime_are_sent_before_the_current_one(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    client.down = False
    outcome = service.update(PATH, episode(7))
    assert client.saves == [(1, Status.CURRENT, 5), (1, Status.CURRENT, 7)]
    assert outcome.message.endswith("(+1 queued sent)")
    assert not queue.exists()


def test_a_queued_first_episode_of_a_rewatch_is_sent_before_the_second(tmp_path):
    client = Flaky(by_id={1: media(1, 12, entry={"status": "COMPLETED", "progress": 12})})
    service = queue_make(tmp_path, client, set_completed_to_rewatching_on_first_episode=True)
    client.down = True
    service.update(PATH, episode(1))
    client.down = False
    service.update(PATH, episode(2))
    assert client.saves == [(1, Status.REPEATING, 0), (1, None, 1), (1, Status.CURRENT, 2)]
    assert not (tmp_path / "queue.json").exists()


def test_a_skipped_update_still_sends_the_queue_since_anilist_answered(offline):
    client, service, queue = offline
    service.update("/other/Show 2 - 02.mkv", episode(2, anime_id=2, name="Other"))
    client.down = False
    with pytest.raises(SkipUpdate, match="not new"):
        service.update(PATH, episode(3))
    assert client.saves == [(2, Status.CURRENT, 2)]
    assert not queue.exists()


def test_the_message_counts_the_queued_updates_that_were_given_up_on(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    client.down = False
    client.by_id[1] = media(1, 12, entry={"status": "CURRENT", "progress": 8})
    outcome = service.update("/other/Show 2 - 02.mkv", episode(2, anime_id=2, name="Other"))
    assert outcome.message.endswith("(+1 dropped)")
    assert not queue.exists()


def test_queued_episodes_of_one_anime_read_the_entry_once(offline):
    client, service, _ = offline
    service.update(PATH, episode(5))
    service.update(PATH, episode(6))
    client.down = False
    before = client.requests
    assert service.drain().sent == 2
    assert client.requests == before + 1


def test_queued_episode_already_watched_elsewhere_is_dropped(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    client.down = False
    client.by_id[1] = media(1, 12, entry={"status": "CURRENT", "progress": 8})
    assert service.drain().sent == 0
    assert client.saves == []
    assert not queue.exists()


def test_failing_in_the_middle_keeps_the_rest(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    service.update(PATH, episode(2, anime_id=2, name="Other"))
    client.down = False
    save = client.save_entry

    def die_on_second(anime_id, *args, **kwargs):
        if anime_id == 2:
            raise AniListError("rate limited", 429, retryable=True)
        return save(anime_id, *args, **kwargs)

    client.save_entry = die_on_second
    assert service.drain().sent == 1
    left = json.loads(queue.read_text())
    assert [(i["info"]["anime_id"], i["info"]["episode"]) for i in left] == [(2, 2)]


def test_killed_after_the_save_before_the_removal_sends_nothing_twice(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    client.down = False

    def killed(*_):
        raise KeyboardInterrupt

    service.pending.remove = killed
    with pytest.raises(KeyboardInterrupt):
        service.drain()
    assert client.saves == [(1, Status.CURRENT, 5)]
    assert queue.exists()

    client.by_id[1] = media(1, 12, entry={"status": "CURRENT", "progress": 5})
    fresh = queue_make(tmp_path := queue.parent, client)
    assert fresh.drain().sent == 0
    assert client.saves == [(1, Status.CURRENT, 5)]
    assert not (tmp_path / "queue.json").exists()


def test_an_unexpected_failure_keeps_the_item_and_does_not_block_the_rest(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    service.update(PATH, episode(2, anime_id=2, name="Other"))
    client.down = False
    media_by_id = client.media_by_id
    client.media_by_id = lambda i: (_ for _ in ()).throw(KeyError("odd")) if i == 1 else media_by_id(i)

    assert service.drain().sent == 1
    assert [i["info"]["anime_id"] for i in json.loads(queue.read_text())] == [1]
    assert service.update(PATH, episode(5, anime_id=2, name="Other")) is not None


def test_a_missing_token_keeps_the_queue(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    client.down = False
    client.save_entry = lambda *_a, **_k: (_ for _ in ()).throw(AniListError("No AniList token found.", 401))
    assert service.drain().sent == 0
    assert queue.exists()


def test_correction_made_after_the_outage_redirects_the_queued_episode(offline):
    client, service, _ = offline
    service.update(PATH, episode(5))
    client.down = False
    client.by_id[2] = media(2, 12, entry={"status": "CURRENT", "progress": 1})
    service.cache.put(PATH, episode(5, anime_id=2), corrected=True)
    service.drain()
    assert client.saves == [(2, Status.CURRENT, 5)]


def test_correct_sends_the_queue_after_applying_the_correction(offline):
    client, service, queue = offline
    service.update(PATH, episode(5))
    client.down = False
    client.by_id[1] = media(1, 12, entry=ENTRY)
    service.correct(PATH, episode(5), Correction(1, status=Status.PAUSED))
    assert (1, Status.CURRENT, 5) in client.saves
    assert not queue.exists()


def test_a_broken_queue_file_does_not_break_the_action(offline):
    client, service, queue = offline
    client.down = False
    queue.write_text("{not json", encoding="utf-8")
    assert service.drain().sent == 0
    queue.write_text(json.dumps([{"path": 3}, {"path": PATH, "info": [1]}, "x"]), encoding="utf-8")
    assert service.drain().sent == 0


def test_update_on_the_outage_403_is_queued(tmp_path):
    client = Flaky(AniListError("temporarily disabled", 403, retryable=True), by_id={1: media(1, 12, entry=ENTRY)})
    service = queue_make(tmp_path, client)
    client.down = True
    assert "will be sent later" in service.update(PATH, episode(5)).message
    assert (tmp_path / "queue.json").exists()


def test_a_stale_cache_hit_that_cannot_be_refreshed_is_still_detected(tmp_path):
    client = Flaky(by_id={1: media(1, 12, entry=ENTRY)})
    service = queue_make(tmp_path, client, cache_mode="SLIDING")
    service.cache.put(PATH, replace(episode(5), total_episodes=None))
    service.pending.add(PATH, episode(4))
    client.down = True

    info = service.info(PATH).info
    assert (info.anime_id, info.episode) == (1, 5)
    assert client.requests == 1  # the refresh; nothing was sent after it failed
    assert service.pending.items()
