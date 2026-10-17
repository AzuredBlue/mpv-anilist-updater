import json

from anilist_updater.cache import CORRECTED_TTL, Cache, _key
from anilist_updater.models import AnimeInfo, Options, Status

PATH = "/anime/Show/Show - 13.mkv"
INFO = AnimeInfo(
    anime_id=7,
    anime_name="Show 2",
    episode=1,
    current_progress=0,
    total_episodes=12,
    current_status=Status.CURRENT,
    guessed_name="Show",
    absolute_episode=13,
)


class Clock:
    now = 1_000_000.0

    def __call__(self):
        return self.now


def make(tmp_path, clock=None, **options):
    return Cache(Options(**options), tmp_path / "cache.json", clock or Clock())


def test_round_trip_and_episode_mapping(tmp_path):
    cache = make(tmp_path)
    cache.put(PATH, INFO)
    cache.save()

    entry = make(tmp_path).get(PATH, "Show")
    assert entry.offset == 12
    assert entry.info_for(14).episode == 2
    assert entry.info_for(12) is None
    assert entry.info_for(26) is None


def test_other_title_or_directory_misses(tmp_path):
    cache = make(tmp_path)
    cache.put(PATH, INFO)
    assert cache.get(PATH, "Other") is None
    assert cache.get("/elsewhere/file.mkv", "Show") is None


def test_normal_entry_expires(tmp_path):
    clock = Clock()
    cache = make(tmp_path, clock, cache_refresh_rate=1)
    cache.put(PATH, INFO)
    cache.save()
    clock.now += 3601
    assert make(tmp_path, clock, cache_refresh_rate=1).get(PATH, "Show") is None


def test_normal_entry_does_not_slide(tmp_path):
    clock = Clock()
    cache = make(tmp_path, clock, cache_refresh_rate=10)
    cache.put(PATH, INFO)
    ttl = cache.get(PATH, "Show").ttl
    clock.now += 30_000
    assert cache.get(PATH, "Show").ttl == ttl


def test_sliding_extends_only_past_half_of_the_window(tmp_path):
    clock = Clock()
    cache = make(tmp_path, clock, cache_refresh_rate=10, cache_mode="sliding")
    cache.put(PATH, INFO)
    ttl = cache.get(PATH, "Show").ttl
    clock.now += 10_000
    assert cache.get(PATH, "Show").ttl == ttl
    clock.now += 10_000
    assert cache.get(PATH, "Show").ttl == clock.now + 36_000


def test_corrected_entries_last_longer_and_stay_corrected(tmp_path):
    clock = Clock()
    cache = make(tmp_path, clock)
    cache.put(PATH, INFO, corrected=True)
    cache.put(PATH, INFO)
    entry = cache.get(PATH, "Show")
    assert entry.corrected
    assert entry.ttl == clock.now + CORRECTED_TTL


def test_legacy_entry_is_migrated(tmp_path):
    legacy = {
        _key(PATH): {
            "guessed_name": "Show",
            "anime_name": "Show 2",
            "anime_id": 7,
            "mal_id": None,
            "current_progress": 3,
            "relative_progress": "19->7",
            "total_episodes": 12,
            "current_status": "CURRENT",
            "corrected": True,
            "ttl": Clock.now + 100,
        },
        "broken": {"guessed_name": "x"},
    }
    (tmp_path / "cache.json").write_text(json.dumps(legacy), encoding="utf-8")

    cache = make(tmp_path)
    assert cache.get(PATH, "Show").info_for(20).episode == 8
    cache.save()

    saved = json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))
    assert list(saved) == [_key(PATH)]
    assert saved[_key(PATH)]["offset"] == 12


def test_unchanged_cache_is_not_rewritten(tmp_path):
    cache = make(tmp_path)
    cache.get(PATH, "Show")
    cache.save()
    assert not (tmp_path / "cache.json").exists()


def test_corrupt_file_starts_empty(tmp_path):
    (tmp_path / "cache.json").write_text("{nope", encoding="utf-8")
    assert make(tmp_path).get(PATH, "Show") is None
