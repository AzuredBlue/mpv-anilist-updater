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


def test_main_series_ignores_shorts_next_to_a_full_series():
    short = media(1, 12, duration=3)
    assert main_series([short]) == [short]
    assert [m["id"] for m in main_series([short, SEASONS[0]])] == [1]
    full = media(5, 12, sequel=6)
    assert [m["id"] for m in main_series([media(6, 12, fmt="TV_SHORT", prequel=5), full])] == [5]


def test_main_series_of_shorts_is_followed_across_a_movie():
    s1 = media(1, 12, fmt="TV_SHORT", duration=12, sequel=2)
    s2 = media(2, 12, fmt="TV_SHORT", duration=12, sequel=3, prequel=1)
    movie = media(3, 1, fmt="MOVIE", duration=112, sequel=4, prequel=2)
    s3 = media(4, 11, fmt="TV_SHORT", duration=12, prequel=3)
    assert [m["id"] for m in main_series([s3, movie, s2, s1], "Show")] == [1, 2, 4]
    info = resolve(FakeClient(everything=[s3, movie, s2, s1]), FileInfo("Show", 25))
    assert (info.anime_id, info.episode) == (4, 1)


def test_resolve_bare_title_of_shorts_goes_to_the_first_season():
    s1 = media(1, 12, fmt="TV_SHORT", duration=2, sequel=2)
    s2 = media(2, 12, fmt="TV_SHORT", duration=2, prequel=1)
    assert resolve(FakeClient(everything=[s2, s1]), FileInfo("Show", 5)).anime_id == 1


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


class RankedClient(FakeClient):
    """AniList ranks the last season first."""

    def __init__(self, seasons):
        super().__init__(everything=[seasons[-1], *seasons[:-1]])


def test_resolve_absolute_episode_starts_at_the_first_season_not_the_best_result():
    client = RankedClient([media(1, 12, sequel=2), media(2, 12, sequel=3, prequel=1), media(3, 12, prequel=2)])
    assert [(r.anime_id, r.episode) for r in (resolve(client, FileInfo("Show", e)) for e in (15, 30))] == [
        (2, 3),
        (3, 6),
    ]


def _three(**entries):
    return [
        media(i, 12, sequel=i + 1 if i < 3 else None, prequel=i - 1 or None, entry=entries.get(f"s{i}"))
        for i in (1, 2, 3)
    ]


def test_resolve_bare_title_goes_to_the_first_season_not_the_best_result():
    info = resolve(RankedClient(_three()), FileInfo("Show", 5))
    assert (info.anime_id, info.episode) == (1, 5)


def test_resolve_bare_title_stops_at_the_season_being_watched():
    current = {"status": "CURRENT", "progress": 4}
    done = {"status": "COMPLETED", "progress": 12}
    assert resolve(RankedClient(_three(s1=done, s2=done, s3=current)), FileInfo("Show", 5)).anime_id == 3
    assert resolve(RankedClient(_three(s2=current)), FileInfo("Show", 5)).anime_id == 2


def test_resolve_bare_title_walks_through_seasons_missing_from_the_list():
    seasons = _three(s1={"status": "CURRENT", "progress": 4}, s3={"status": "PLANNING", "progress": 0})
    client = FakeClient(user=[seasons[2], seasons[0]], everything=[seasons[2], seasons[1], seasons[0]])
    assert resolve(client, FileInfo("Show", 5)).anime_id == 1


def test_resolve_season_in_the_file_is_not_walked_back():
    info = resolve(RankedClient(_three()), FileInfo("Show", 5, season=3))
    assert (info.anime_id, info.episode) == (3, 5)


def test_resolve_absolute_episode_counts_from_the_entry_the_file_names():
    seasons = [media(1, 12, sequel=2), media(2, 12, sequel=3, prequel=1), media(3, 12, prequel=2)]
    client = FakeClient(everything=seasons[1:])
    info = resolve(client, FileInfo("Show", 13, season=2))
    assert (info.anime_id, info.episode) == (3, 1)


def test_main_series_does_not_walk_back_to_an_entry_with_another_name():
    old = media(1, 12, sequel=2, title="Bleach")
    new = media(2, 13, prequel=1, title="Bleach: Thousand-Year Blood War")
    assert [m["id"] for m in main_series([new, old], "Bleach Thousand Year Blood War")] == [2]
    assert [m["id"] for m in main_series([new, old], "Bleach")] == [1, 2]


def test_main_series_stops_at_several_prequels():
    a, b = media(1, 12, sequel=3), media(2, 12, sequel=3)
    last = media(3, 12)
    last["relations"]["edges"] = [{"relationType": "PREQUEL", "node": {"id": i, "format": "TV"}} for i in (1, 2)]
    assert [m["id"] for m in main_series([last, a, b], "Show")] == [3]


def test_main_series_picks_the_sequel_named_like_the_title():
    first = media(1, 12, sequel=2, title="Show")
    first["relations"]["edges"].append({"relationType": "SEQUEL", "node": {"id": 3, "format": "TV"}})
    named, other = media(2, 12, prequel=1, title="Show 2"), media(3, 12, prequel=1, title="Other")
    assert [m["id"] for m in main_series([first, other, named], "Show")] == [1, 2]
    assert [m["id"] for m in main_series([first, other, named])] == [1]


class UndatedClient(FakeClient):
    """The year filter drops the first cour."""

    def __init__(self):
        super().__init__()
        self.years = []

    def search(self, title, year, formats):
        self.years.append(year)
        return ([], SEASONS[1:]) if year else ([], SEASONS)


def test_resolve_year_that_drops_the_first_season_is_searched_without_it():
    client = UndatedClient()
    info = resolve(client, FileInfo("Show", 40, year=2023))
    assert (info.anime_id, info.episode) == (3, 4)
    assert client.years == [2023, None]


def test_resolve_absolute_episode_does_not_count_a_movie_between_seasons():
    movie = media(10, 1, fmt="MOVIE", sequel=2, prequel=1)
    seasons = [media(1, 12, sequel=10), movie, media(2, 12, prequel=10)]
    client = FakeClient(everything=seasons)
    assert [(i.anime_id, i.episode) for i in (resolve(client, FileInfo("Show", e)) for e in (12, 13, 24))] == [
        (1, 12),
        (2, 1),
        (2, 12),
    ]
    with pytest.raises(SkipUpdate):
        resolve(client, FileInfo("Show", 25))
