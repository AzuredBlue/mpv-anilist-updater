import pytest
from fakes import SEASONS, FakeClient, media

from anilist_updater.errors import SkipUpdate
from anilist_updater.models import FileInfo, Status
from anilist_updater.resolve import locate_episode, main_series, refresh, resolve


def test_main_series_follows_sequels_in_order():
    shuffled = [SEASONS[0], SEASONS[2], SEASONS[1]]
    assert [m["id"] for m in main_series(shuffled)] == [1, 2, 3]


def test_main_series_skips_movies_but_keeps_following():
    movie = media(10, 1, fmt="MOVIE", sequel=2)
    first = media(1, 12, sequel=10)
    assert [m["id"] for m in main_series([first, movie, SEASONS[1]])] == [1, 2]


def test_main_series_stops_at_cycles_and_missing_sequels():
    looping = [media(1, 12, sequel=2), media(2, 12, sequel=1)]
    assert [m["id"] for m in main_series(looping)] == [1, 2]
    assert [m["id"] for m in main_series([media(1, 12, sequel=99)])] == [1]


def test_main_series_ignores_shorts():
    assert main_series([media(1, 12, duration=3)]) is None


@pytest.mark.parametrize(
    ("absolute", "season", "relative"),
    [(1, 1, 1), (12, 1, 12), (13, 2, 1), (36, 2, 24), (37, 3, 1), (200, 3, 164)],
)
def test_locate_episode(absolute, season, relative):
    found, episode = locate_episode(SEASONS, absolute)
    assert (found["id"], episode) == (season, relative)


def test_locate_episode_past_known_seasons():
    assert locate_episode(SEASONS[:2], 37) is None
    assert locate_episode(None, 1) is None


def test_resolve_prefers_user_list_and_reads_entry():
    entry = {"status": "CURRENT", "progress": 3}
    client = FakeClient(user=[media(1, 12, entry=entry)], everything=[media(9, 12)])
    info = resolve(client, FileInfo("Show", 4))
    assert (info.anime_id, info.episode, info.current_progress, info.current_status) == (1, 4, 3, Status.CURRENT)
    assert (info.guessed_name, info.absolute_episode) == ("Show", 4)


def test_resolve_absolute_episode_across_seasons():
    client = FakeClient(user=SEASONS)
    info = resolve(client, FileInfo("Show", 15))
    assert (info.anime_id, info.episode, info.absolute_episode) == (2, 3, 15)


def test_resolve_falls_back_to_global_when_user_list_lacks_seasons():
    client = FakeClient(user=SEASONS[:1], everything=SEASONS)
    assert resolve(client, FileInfo("Show", 15)).anime_id == 2


def test_resolve_nothing_found():
    with pytest.raises(SkipUpdate, match="Couldn't find"):
        resolve(FakeClient(), FileInfo("Nope", 1))


def test_resolve_absolute_episode_out_of_range():
    with pytest.raises(SkipUpdate, match="No valid seasons"):
        resolve(FakeClient(user=SEASONS[:2]), FileInfo("Show", 99))


def test_refresh_fills_episode_count_and_drops_progress_without_entry():
    client = FakeClient(by_id={1: media(1, 12)})
    base = resolve(
        FakeClient(user=[media(1, None, entry={"status": "CURRENT", "progress": 2})]), FileInfo("Show", 3)
    )
    refreshed = refresh(client, base)
    assert (refreshed.total_episodes, refreshed.current_progress, refreshed.current_status) == (12, None, None)


def test_refresh_to_another_anime_drops_old_progress():
    client = FakeClient(by_id={2: media(2, 24)})
    base = resolve(
        FakeClient(user=[media(1, 12, entry={"status": "CURRENT", "progress": 2})]), FileInfo("Show", 3)
    )
    switched = refresh(client, base, 2)
    assert (switched.anime_id, switched.current_progress, switched.current_status) == (2, None, None)


class TitleClient(FakeClient):
    """Only the base title is known to AniList; later seasons carry other names."""

    def search(self, title, year, formats):
        return ([], []) if title.endswith("Season 2") else (self.user, self.everything)


def test_resolve_counts_sequels_when_the_season_title_is_unknown():
    info = resolve(TitleClient(everything=SEASONS), FileInfo("Show", 3, season=2))
    assert (info.anime_id, info.episode, info.guessed_name) == (2, 3, "Show Season 2")


class SeasonClient(FakeClient):
    """The season title finds only that season; the plain title finds the whole series."""

    def search(self, title, year, formats):
        return ([], [media(2, 12)]) if title == "Show Season 2" else ([], SEASONS)


def test_resolve_folder_season_that_cannot_hold_the_episode_is_absolute():
    info = resolve(SeasonClient(), FileInfo("Show", 15, season=2, folder_season=True))
    assert (info.anime_id, info.episode, info.absolute_episode, info.guessed_name) == (2, 3, 15, "Show Season 2")


def test_resolve_folder_season_that_holds_the_episode_is_kept():
    info = resolve(SeasonClient(), FileInfo("Show", 5, season=2, folder_season=True))
    assert (info.anime_id, info.episode) == (2, 5)


class UnnamedSeasonsClient(FakeClient):
    """AniList knows the show, but no title ending in a season number."""

    def search(self, title, year, formats):
        return ([], []) if "Season" in title else ([], self.everything)


def test_resolve_folder_season_not_on_anilist_counts_from_the_first_season():
    seasons = [media(1, 12, sequel=2), media(2, 12, sequel=3), media(3, 12)]
    info = resolve(UnnamedSeasonsClient(everything=seasons), FileInfo("Show", 30, season=3, folder_season=True))
    assert (info.anime_id, info.episode, info.guessed_name) == (3, 6, "Show Season 3")


def test_resolve_folder_season_not_on_anilist_is_not_guessed_from_the_first_season():
    seasons = [media(1, 12, sequel=2), media(2, 12)]
    with pytest.raises(SkipUpdate, match="Couldn't find"):
        resolve(UnnamedSeasonsClient(everything=seasons), FileInfo("Show", 3, season=5, folder_season=True))


def test_resolve_season_beyond_the_known_ones_is_not_found():
    with pytest.raises(SkipUpdate, match="Couldn't find"):
        resolve(TitleClient(everything=SEASONS[:1]), FileInfo("Show", 1, season=2))


def test_resolve_later_season_prefers_a_real_season_over_its_specials():
    client = FakeClient(everything=[media(5, 3, fmt="SPECIAL"), media(2, 12)])
    assert resolve(client, FileInfo("Show", 3, season=2)).anime_id == 2


def test_resolve_keeps_the_first_result_when_the_file_names_a_part_or_a_format():
    client = FakeClient(everything=[media(5, 3, fmt="SPECIAL"), media(2, 12)])
    assert resolve(client, FileInfo("Show", 1, season=2, part=1)).anime_id == 5
    assert resolve(client, FileInfo("Show", 1, season=2, media_format="SPECIAL")).anime_id == 5


class NumberedClient(FakeClient):
    """AniList knows "Show" as a short series, so episode 100 fits no season; `answer` is what "Show 100" finds."""

    def __init__(self, answer):
        super().__init__()
        self.answer, self.queries = answer, []

    def search(self, title, year, formats):
        self.queries.append(title)
        return ([], self.answer) if title == "Show 100" else ([], [media(1, 12)])


def test_resolve_title_ending_in_a_number_is_found_again_with_it():
    info = resolve(NumberedClient([media(100, 12)]), FileInfo("Show", 100))
    assert (info.anime_id, info.episode, info.absolute_episode, info.guessed_name) == (100, 1, 100, "Show")


def test_resolve_title_with_another_number_is_not_taken_for_the_episode_number():
    client = NumberedClient([media(7, 12)])
    with pytest.raises(SkipUpdate, match="No valid seasons"):
        resolve(client, FileInfo("Show", 100))
    assert client.queries == ["Show", "Show 100"]


class RecordingClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.queries = []

    def search(self, title, year, formats):
        self.queries.append(title)
        return super().search(title, year, formats)


def test_resolve_title_not_found_at_all_is_not_searched_again_with_the_episode_number():
    client = RecordingClient()
    with pytest.raises(SkipUpdate, match="Couldn't find"):
        resolve(client, FileInfo("Show", 2))
    assert client.queries == ["Show"]


class OutOfRangeClient(FakeClient):
    """ "Show" is found, but its one season is too short for the episode; "Show 13" names another entry."""

    def search(self, title, year, formats):
        return ([], [media(13, 12)]) if title == "Show 13" else ([], [media(1, 12)])


def test_resolve_small_episode_past_the_seasons_is_read_as_part_of_the_title():
    info = resolve(OutOfRangeClient(), FileInfo("Show", 13))
    assert (info.anime_id, info.episode, info.absolute_episode) == (13, 1, 13)


class SpecialsOnlyClient(FakeClient):
    """ "Show Season 2" matches only the specials; the plain title finds the whole series."""

    def search(self, title, year, formats):
        return ([], [media(5, 3, fmt="SPECIAL")]) if title == "Show Season 2" else ([], SEASONS)


def test_resolve_later_season_with_only_specials_found_counts_sequels():
    assert resolve(SpecialsOnlyClient(), FileInfo("Show", 3, season=2)).anime_id == 2


def test_resolve_later_season_with_only_specials_found_and_no_sequels_keeps_them():
    client = FakeClient(everything=[media(5, 3, fmt="SPECIAL")])
    assert resolve(client, FileInfo("Show", 1, season=2)).anime_id == 5


class FormatClient(FakeClient):
    def __init__(self):
        super().__init__(everything=[media(1, 12)])
        self.formats = None

    def search(self, title, year, formats):
        self.formats = formats
        return super().search(title, year, formats)


def test_resolve_tv_hint_searches_tv_and_short_series():
    client = FormatClient()
    resolve(client, FileInfo("Show", 1, media_format="TV"))
    assert client.formats == ["TV", "TV_SHORT"]


class YearClient(FakeClient):
    """The year filter leaves one season of the chain."""

    def search(self, title, year, formats):
        return ([], [SEASONS[1]]) if year else ([], SEASONS)


def test_resolve_absolute_episode_ignores_a_year_that_fits_one_season_only():
    info = resolve(YearClient(), FileInfo("Show", 40, year=2023))
    assert (info.anime_id, info.episode) == (3, 4)


def test_resolve_keeps_the_year_when_the_episode_fits():
    assert resolve(YearClient(), FileInfo("Show", 5, year=2023)).anime_id == 2
