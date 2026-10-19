from dataclasses import replace

import pytest
from fakes import SEASONS, FakeClient, media

from anilist_updater.errors import SkipUpdate
from anilist_updater.models import FileInfo, Status
from anilist_updater.parsing import parse_path
from anilist_updater.resolve import TIMES_SIGN, locate_episode, main_series, refresh, resolve


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


def test_resolve_nothing_found_for_a_title_with_marks():
    """ "Magical Taruruuto-kun Movie 3 Sukisuki Takoyaki!" found nothing and indexed the empty results."""
    with pytest.raises(SkipUpdate, match="Couldn't find"):
        resolve(FakeClient(), FileInfo("Sukisuki Takoyaki", 1, marks="!"))


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


def test_mal_id_follows_the_resolved_and_refreshed_anime():
    info = resolve(FakeClient(user=[media(1, 12, mal=100)]), FileInfo("Show", 3))
    assert info.mal_id == 100
    assert refresh(FakeClient(by_id={2: media(2, 24, mal=200)}), info, 2).mal_id == 200
    assert refresh(FakeClient(by_id={2: media(2, 24)}), info, 2).mal_id is None


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


def test_resolve_user_list_hit_that_does_not_name_the_title_loses_to_the_global_one():
    client = FakeClient(user=[media(9, 12, title="Other Show")], everything=[media(1, 13, title="K-ON!")])
    assert resolve(client, FileInfo("K ON", 2)).anime_id == 1


def test_resolve_user_list_is_kept_when_both_name_the_title_alike():
    client = FakeClient(user=[media(9, 12, title="Show Two")], everything=[media(1, 12, title="Show Three")])
    assert resolve(client, FileInfo("Show", 2)).anime_id == 9
    client = FakeClient(user=[media(9, 12, title="Show")], everything=[media(1, 12, title="Show")])
    assert resolve(client, FileInfo("Show", 2)).anime_id == 9


def test_resolve_entry_called_exactly_like_the_title_goes_before_longer_ones():
    fan_letter = media(1, 1, fmt="SPECIAL", title="Show Fan Letter")
    client = FakeClient(everything=[fan_letter, media(2, 100, title="Show")])
    assert resolve(client, FileInfo("Show", 1)).anime_id == 2


class AliasClient(FakeClient):
    """AniList needs every word, and knows the show by a shorter name."""

    def search(self, title, year, formats):
        return ([], [media(1, 12, title="Hai to Gensou no Grimgar")]) if title == "Hai to Gensou" else ([], [])


def test_resolve_title_with_an_extra_subtitle_is_searched_shortened():
    assert resolve(AliasClient(), FileInfo("Hai to Gensou Ashes and Illusions", 1)).anime_id == 1


def test_resolve_shortened_search_keeps_at_least_two_words():
    with pytest.raises(SkipUpdate, match="Couldn't find"):
        resolve(AliasClient(), FileInfo("Hai Ashes and Illusions", 1))


class GraphClient(FakeClient):
    """Search finds the latest season only; the earlier ones come by ID."""

    def __init__(self, found, seasons):
        super().__init__(everything=found)
        self.seasons = {m["id"]: m for m in seasons}

    def related(self, ids):
        return {i: self.seasons[i] for i in ids if i in self.seasons}

    def search(self, title, year, formats):
        return ([], self.everything) if title == "Show Bloom" else ([], [])


BLOOM = [media(1, 12), media(2, 12, sequel=3, prequel=1), media(3, 12, prequel=2, title="Show Bloom")]


def test_resolve_season_in_the_title_is_checked_by_counting_the_seasons_before_it():
    client = GraphClient(BLOOM[2:], BLOOM)
    assert resolve(client, FileInfo("Show Bloom", 1, season=3)).anime_id == 3


def test_resolve_title_naming_a_later_season_exactly_wins_over_the_season_number():
    assert resolve(GraphClient(BLOOM[2:], BLOOM), FileInfo("Show Bloom", 1, season=2)).anime_id == 3


def test_resolve_shortened_title_is_not_taken_for_a_season_it_is_not():
    with pytest.raises(SkipUpdate):
        resolve(GraphClient(BLOOM[:1], BLOOM), FileInfo("Show Bloom", 1, season=3))


def test_main_series_fetches_the_relations_the_results_lack():
    seasons = [media(1, 12, sequel=2), media(2, 12, prequel=1)]
    assert [m["id"] for m in main_series(seasons[:1], fetch=lambda _ids: {2: seasons[1]})] == [1, 2]


def test_resolve_absolute_number_beside_the_episode_picks_the_season_it_falls_in():
    client = FakeClient(everything=SEASONS)
    assert (info := resolve(client, FileInfo("Show", 3, season=1, absolute=15))).anime_id == 2
    assert (info.episode, info.absolute_episode) == (3, 3)


def test_resolve_absolute_number_in_one_long_entry_is_that_entry():
    client = FakeClient(everything=[media(1, 170)])
    assert (info := resolve(client, FileInfo("Show", 12, season=2, absolute=63))).anime_id == 1
    assert info.episode == 63


def test_resolve_episode_past_the_first_hit_is_found_in_a_remake_of_the_same_name():
    client = FakeClient(everything=[media(1, 14, title="Show"), media(2, 28, title="Show (2009)")])
    assert resolve(client, FileInfo("Show", 16)).anime_id == 2
    assert resolve(client, FileInfo("Show", 2)).anime_id == 1


def test_resolve_special_with_a_tv_slot_number_is_the_only_episode_of_its_entry():
    client = FakeClient(everything=[media(5, 1, fmt="OVA", title="Show Special")])
    info = resolve(client, FileInfo("Show", 14, media_format="SPECIAL"))
    assert (info.anime_id, info.episode) == (5, 1)


def test_resolve_ova_searches_its_own_format_and_the_special_one_only_when_it_finds_nothing():
    client = FormatClient()
    resolve(client, FileInfo("Show", 1, media_format="OVA"))
    assert client.formats == ["OVA"]

    class OtherKindClient(FakeClient):
        def search(self, title, year, formats):
            return ([], [media(5, 1, fmt="SPECIAL", title="Show")]) if formats == ["SPECIAL"] else ([], [])

    assert resolve(OtherKindClient(), FileInfo("Show", 1, media_format="OVA")).anime_id == 5

    class BothClient(FakeClient):
        def search(self, title, year, formats):
            kind = formats[0]
            return ([], [media(5 if kind == "SPECIAL" else 6, 2, fmt=kind, title="Show")])

    assert resolve(BothClient(), FileInfo("Show", 1, media_format="OVA")).anime_id == 6


class ContinuedClient(FakeClient):
    """The season's own title finds that season only; the next one comes by ID."""

    def related(self, ids):
        return {3: media(3, 12, prequel=2)} if 3 in ids else {}

    def search(self, title, year, formats):
        return ([], [media(2, 12, sequel=3, prequel=1)]) if title == "Show Season 2" else ([], [])


def test_resolve_season_numbering_goes_on_into_the_next_entry():
    info = resolve(ContinuedClient(), FileInfo("Show", 20, season=2))
    assert (info.anime_id, info.episode, info.absolute_episode) == (3, 8, 20)
    assert resolve(ContinuedClient(), FileInfo("Show", 5, season=2)).anime_id == 2


class AltNameClient(FakeClient):
    def search(self, title, year, formats):
        return ([], [media(1, 12, title="Other Name")]) if title == "Other Name" else ([], [])


def test_resolve_other_name_in_the_file_is_searched_when_the_title_finds_nothing():
    assert resolve(AltNameClient(), FileInfo("Primary Name", 1, alt_title="Other Name")).anime_id == 1
    with pytest.raises(SkipUpdate):
        resolve(AltNameClient(), FileInfo("Primary Name", 1))


def test_resolve_season_count_crosses_an_ova_between_seasons():
    seasons = [
        media(1, 12, sequel=2),
        media(2, 12, sequel=4, prequel=1),
        media(4, 1, fmt="OVA", sequel=3, prequel=2),
        media(3, 12, prequel=4, title="Show Bloom"),
    ]
    assert resolve(GraphClient(seasons[3:], seasons), FileInfo("Show Bloom", 1, season=3)).anime_id == 3


class PartClient(FakeClient):
    """ "Show Season 2 Part 2" finds the second part; its files go on numbering from the first."""

    def related(self, ids):
        return {1: media(1, 12, sequel=2), 2: media(2, 12, prequel=1, sequel=3), 3: media(3, 12, prequel=2)}

    def search(self, title, year, formats):
        return ([], [media(2, 12, prequel=1, sequel=3)]) if "Part 2" in title else ([], [])


def test_resolve_part_numbering_goes_on_from_the_earlier_part():
    assert (info := resolve(PartClient(), FileInfo("Show", 14, season=2, part=2))).anime_id == 2
    assert info.episode == 2
    assert resolve(PartClient(), FileInfo("Show", 5, season=2, part=2)).episode == 5


def test_resolve_shortened_search_is_not_a_guess_for_a_file_that_names_a_format():
    with pytest.raises(SkipUpdate):
        resolve(AliasClient(), FileInfo("Hai to Gensou Ashes and Illusions", 1, media_format="OVA"))


def test_resolve_one_word_other_name_is_too_vague_to_search():
    with pytest.raises(SkipUpdate):
        resolve(AltNameClient(), FileInfo("Primary Name", 1, alt_title="Other"))


def test_resolve_tv_series_naming_the_title_goes_before_a_one_off_with_that_synonym():
    crossover = media(1, 1, fmt="ONA", title="Show x Town")
    crossover["synonyms"] = ["Show"]
    client = FakeClient(everything=[crossover, media(2, 13, title="Show Season 1")])
    assert resolve(client, FileInfo("Show", 1, season=1)).anime_id == 2
    assert resolve(client, FileInfo("Show", 1, media_format="ONA")).anime_id == 1


def test_resolve_entry_titled_exactly_like_the_search_stays_before_a_tv_series():
    client = FakeClient(everything=[media(1, 110, fmt="OVA", title="Show"), media(2, 12, title="Show New Thesis")])
    assert resolve(client, FileInfo("Show", 1)).anime_id == 1


def test_resolve_season_one_in_the_name_goes_back_to_the_first_season():
    client = FakeClient(
        everything=[media(2, 12, prequel=1, title="Show Two"), media(1, 12, sequel=2, title="Show")]
    )
    assert resolve(client, FileInfo("Show", 3, season=1)).anime_id == 1


class ArcClient(FakeClient):
    """The title is the arc's own name: counting the chain would land on the third entry, a different arc."""

    def search(self, title, year, formats):
        seasons = [
            media(1, 12, sequel=2),
            media(2, 12, prequel=1, sequel=3),
            media(3, 12, prequel=2, title="Show Three"),
            media(4, 12, prequel=2, title="Show Arc"),
        ]
        return ([], seasons) if title == "Show Arc" else ([], [])


def test_resolve_season_is_the_entry_the_title_names_not_the_third_one_counted():
    assert resolve(ArcClient(), FileInfo("Show Arc", 1, season=3)).anime_id == 4


def test_resolve_entry_with_the_exact_synonym_goes_before_one_that_only_ranks_higher():
    named = media(2, 11, title="Show New World")
    named["synonyms"] = ["Show Season 3"]
    client = FakeClient(everything=[media(1, 13, title="Show Future Part 3"), named])
    assert resolve(client, FileInfo("Show", 1, season=3)).anime_id == 2


def test_resolve_numbering_goes_on_from_the_earlier_entries_of_the_title():
    client = FakeClient(
        everything=[media(1, 8, sequel=2, title="Show Prelude"), media(2, 8, prequel=1, title="Show Frost")]
    )
    assert (info := resolve(client, FileInfo("Show Frost", 10))).anime_id == 2
    assert info.episode == 2
    assert resolve(client, FileInfo("Show Frost", 5)).episode == 5
    with pytest.raises(SkipUpdate):
        resolve(client, FileInfo("Show Frost", 30))


def test_resolve_films_numbered_like_episodes_are_counted_along_their_series():
    client = FakeClient(
        everything=[
            media(3, 1, fmt="MOVIE", prequel=2, title="Show III"),
            media(2, 1, fmt="MOVIE", prequel=1, sequel=3, title="Show II"),
            media(1, 1, fmt="MOVIE", sequel=2, title="Show I"),
        ]
    )
    assert [resolve(client, FileInfo("Show", n)).anime_id for n in (2, 3)] == [2, 3]
    with pytest.raises(SkipUpdate):
        resolve(client, FileInfo("Show", 4))


class SubtitleClient(FakeClient):
    """AniList names the show only by its subtitle: the franchise word of the release is no part of it."""

    def search(self, title, year, formats):
        named = media(5, 12, title="3-nen Z-gumi Ginpachi-sensei")
        unrelated = media(6, 12, title="3-nen Z-gumi Ginpachi-sensei Plus")
        named["synonyms"] = ["Show: Zany Class"]
        return ([], [unrelated, named]) if title == "3 nen Z gumi Ginpachi sensei" else ([], [])


def test_resolve_title_without_its_first_words_is_searched_when_the_whole_finds_nothing():
    assert resolve(SubtitleClient(), FileInfo("Show 3 nen Z gumi Ginpachi sensei", 2)).anime_id == 5
    ova = FileInfo("Show 3 nen Z gumi Ginpachi sensei", 2, media_format="OVA")
    assert resolve(SubtitleClient(), ova).anime_id == 5
    with pytest.raises(SkipUpdate):
        resolve(SubtitleClient(), FileInfo("Other 3 nen Z gumi Ginpachi sensei", 2))
    with pytest.raises(SkipUpdate):  # too short to be more than a coincidence
        resolve(SubtitleClient(), FileInfo("Show of the Ring", 2))


class HonorificClient(FakeClient):
    """AniList spells the honorific apart: "Show Oji-san" for the release's "Show Ojisan"."""

    def search(self, title, year, formats):
        return ([], [media(7, 13, title="Show Oji-san")]) if title == "Show Oji san" else ([], [])


def test_resolve_honorific_glued_to_its_name_is_searched_apart_when_the_whole_finds_nothing():
    assert resolve(HonorificClient(), FileInfo("Show Ojisan", 3)).anime_id == 7


def test_resolve_folder_season_without_such_season_uses_the_show_as_a_whole():
    """ "Rurouni Kenshin (1996) S02/… - 28 - S02E01": AniList has the 94 episodes in one entry."""
    info = resolve(FakeClient(everything=[media(45, 94)]), FileInfo("Show", 28, season=2, folder_season=True))
    assert (info.anime_id, info.episode) == (45, 28)


def _years(*starts):
    """Seasons of "Show" that began in the given years, newest first as AniList may rank them."""
    seasons = [
        media(i + 1, 12, prequel=i or None, sequel=i + 2 if i + 1 < len(starts) else None, start=year)
        for i, year in enumerate(starts)
    ]
    return list(reversed(seasons))


def test_resolve_year_that_a_later_season_began_in_names_that_season():
    """ "One Punch Man (2025) - 05" is season 3, which began in 2025."""
    client = FakeClient(everything=_years(2015, 2019, 2025))
    assert resolve(client, FileInfo("Show", 5, year=2025)).anime_id == 3
    assert resolve(client, FileInfo("Show", 5)).anime_id == 1


def test_resolve_year_does_not_pin_what_it_cannot_tell_apart():
    # two seasons in one year, a year that is the first season's, and an episode the season cannot hold
    assert resolve(FakeClient(everything=_years(2025, 2025)), FileInfo("Show", 5, year=2025)).anime_id == 1
    assert resolve(FakeClient(everything=_years(2015, 2019, 2025)), FileInfo("Show", 5, year=2015)).anime_id == 1
    assert resolve(FakeClient(everything=_years(2015, 2019, 2025)), FileInfo("Show", 30, year=2025)).anime_id == 3


class SpellingClient(FakeClient):
    """AniList spells "JoJo's Bizarre Adventure", which releases write without the apostrophe."""

    def search(self, title, year, formats):
        names = {"JoJo's Bizarre Adventure": 4}
        return ([], [media(names[title], 12, title=title)]) if title in names else ([], [])


def test_resolve_the_spelling_anilist_uses_is_tried_when_the_title_finds_nothing():
    assert resolve(SpellingClient(), FileInfo("JoJos Bizarre Adventure", 1)).anime_id == 4
    with pytest.raises(SkipUpdate, match="Couldn't find"):
        resolve(SpellingClient(), FileInfo("MS Gundam Wing", 1))


def test_resolve_the_respelled_title_keeps_its_season():
    class Seasons(FakeClient):
        def search(self, title, year, formats):
            if not title.startswith("JoJo's Bizarre Adventure"):
                return [], []
            first = media(1, 12, sequel=2, title="JoJo's Bizarre Adventure")
            second = media(2, 12, prequel=1, title="JoJo's Bizarre Adventure 2nd Season")
            return [], [second, first] if "Season 2" in title else [first, second]

    info = resolve(Seasons(), FileInfo("JoJos Bizarre Adventure", 3, season=2))
    assert (info.anime_id, info.episode) == (2, 3)


def test_resolve_spelling_leaves_words_after_the_first_alone():
    """ "The Apothecary Diaries" must never be searched as "The Apothecary Diarie's"."""
    searched = []

    class Recording(FakeClient):
        def search(self, title, year, formats):
            searched.append(title)
            return [], []

    with pytest.raises(SkipUpdate):
        resolve(Recording(), FileInfo("The Apothecary Diaries", 1, season=2))
    assert not any("'" in text for text in searched)


def test_resolve_spelling_leaves_a_title_with_an_apostrophe_alone():
    searched = []

    class Recording(FakeClient):
        def search(self, title, year, formats):
            searched.append(title)
            return [], []

    with pytest.raises(SkipUpdate):
        resolve(Recording(), FileInfo("Dogs of Cat's Eye", 1))
    assert "Dog's of Cat's Eye" not in searched


def test_resolve_a_title_no_spelling_matches_is_still_not_found():
    with pytest.raises(SkipUpdate, match="Couldn't find"):
        resolve(SpellingClient(), FileInfo("Nothing Like It", 1))


def test_resolve_spelling_is_not_tried_when_the_title_is_found():
    class Found(SpellingClient):
        def search(self, title, year, formats):
            return ([], [media(9, 12, title="JoJos Bizarre Adventure")])

    assert resolve(Found(), FileInfo("JoJos Bizarre Adventure", 1)).anime_id == 9


class WordsClient(FakeClient):
    """AniList's English title has more words in between than the file's other name."""

    def search(self, title, year, formats):
        film = media(9, 1, fmt="MOVIE", title="Show Gaiden")
        film["title"]["english"] = "Show: Legend of Raoh: Chapter of Death in Love"
        return ([], [film]) if title == "Legend of Raoh Death in Love" else ([], [])


def test_resolve_other_name_is_found_by_its_words_but_not_a_shorter_one():
    file = FileInfo("Show 1", 1, media_format="MOVIE", alt_title="Legend of Raoh Death in Love")
    assert resolve(WordsClient(), file).anime_id == 9
    with pytest.raises(SkipUpdate):
        resolve(WordsClient(), replace(file, alt_title="Legend of Raoh"))


class ArcFolderClient(FakeClient):
    """The folder names the arc; AniList splits it into two entries and the file numbers straight through."""

    def search(self, title, year, formats):
        if title == "Show Stardust":
            return [], [
                media(1, 24, sequel=2, title="Show: Stardust"),
                media(2, 24, prequel=1, title="Show: Stardust 2"),
            ]
        if title == "Show Missing":
            return [], [media(5, 12, title="Show")]
        return [], []


def test_resolve_arc_of_the_season_folder_is_searched_and_its_numbering_goes_on():
    assert (info := resolve(ArcFolderClient(), FileInfo("Show", 37, season=2, arc="Stardust"))).anime_id == 2
    assert info.episode == 13
    assert resolve(ArcFolderClient(), FileInfo("Show", 17, season=2, arc="Stardust")).anime_id == 1


def test_resolve_arc_that_no_entry_names_is_no_answer():
    with pytest.raises(SkipUpdate):
        resolve(ArcFolderClient(), FileInfo("Show", 3, season=2, arc="Missing"))


def test_resolve_exclamation_marks_of_the_file_pick_between_entries_that_tie():
    client = FakeClient(
        everything=[media(1, 13, title="Show!"), media(2, 26, title="Show!!"), media(3, 12, title="Show?")]
    )
    assert resolve(client, FileInfo("Show", 1, marks="!!")).anime_id == 2
    assert resolve(client, FileInfo("Show", 1, marks="!")).anime_id == 1
    assert resolve(client, FileInfo("Show", 1)).anime_id == 1
    assert resolve(client, FileInfo("Show", 1, marks="?!")).anime_id == 1


def test_resolve_number_past_the_series_is_the_one_episode_ova_named_after_it():
    extra = media(2, 1, fmt="OVA", title="Show OVA")
    extra["synonyms"] = ["Show Episode 13", "Show Episode 13.5"]
    client = FakeClient(everything=[media(1, 12, sequel=2, title="Show"), extra])
    assert (info := resolve(client, FileInfo("Show", 13))).anime_id == 2
    assert info.episode == 1
    assert resolve(client, FileInfo("Show", 5)).anime_id == 1
    with pytest.raises(SkipUpdate):
        resolve(client, FileInfo("Show", 14))


class FolderTitleClient(FakeClient):
    """AniList calls the season by its stage name, which only the folder gives."""

    def search(self, title, year, formats):
        stage = media(5, 14, prequel=4, title="Show Fifth Stage")
        return (
            ([], [media(4, 24, sequel=5, title="Show Fourth Stage"), stage])
            if title == "Show Fifth Stage"
            else ([], [])
        )


def test_resolve_folder_title_that_goes_on_from_the_file_title_is_searched_when_the_whole_finds_nothing():
    file = FileInfo("Show", 7, season=5, folder_title="Show Fifth Stage")
    assert (info := resolve(FolderTitleClient(), file)).anime_id == 5
    assert info.episode == 7
    with pytest.raises(SkipUpdate):
        resolve(FolderTitleClient(), FileInfo("Show", 7, season=5))


class TimesClient(FakeClient):
    """AniList writes the sequel mark with a times sign, which a search for "X2" doesn't find but for a one-off."""

    def search(self, title, year, formats):
        if title == "Show X2 Ketsu":
            return [], [media(9, 1, fmt="OVA", title="Show Ketsu: Extra")]
        return (
            ([], [media(8, 12, title=f"Show{TIMES_SIGN}2 Ketsu")])
            if title == f"Show {TIMES_SIGN}2 Ketsu"
            else ([], [])
        )


def test_resolve_x2_of_the_name_is_searched_as_the_times_sign_when_the_first_search_fits_nothing():
    assert resolve(TimesClient(), FileInfo("Show X2 Ketsu", 3)).anime_id == 8
    assert resolve(TimesClient(), FileInfo("Show X2 Ketsu", 1, media_format="OVA")).anime_id == 9
    with pytest.raises(SkipUpdate):
        resolve(TimesClient(), FileInfo("Show Max Ketsu", 3))


def test_resolve_walk_back_to_the_first_season_keeps_the_exclamation_marks_of_the_file():
    sequel = media(2, 26, prequel=1, title="Show!!")
    client = FakeClient(everything=[sequel, media(1, 13, sequel=2, title="Show!")])
    assert resolve(client, FileInfo("Show", 1, marks="!!")).anime_id == 2
    assert resolve(client, FileInfo("Show", 1)).anime_id == 1
    both = FakeClient(
        everything=[media(2, 12, prequel=1, title="Show Two!"), media(1, 12, sequel=2, title="Show!")]
    )
    assert resolve(both, FileInfo("Show", 1, marks="!")).anime_id == 1


def test_resolve_title_with_the_files_year_is_not_walked_back_to_the_first_season():
    seasons = [media(1, 12, sequel=2, title="Show"), media(2, 12, prequel=1, title="Show (2018)")]
    assert resolve(FakeClient(everything=[seasons[1], seasons[0]]), FileInfo("Show", 7, year=2018)).anime_id == 2
    assert resolve(FakeClient(everything=[seasons[1], seasons[0]]), FileInfo("Show", 7)).anime_id == 1


def test_resolve_title_with_year_and_later_season_targets_the_later_season():
    # E.g. Ranma 1/2 (2024) Season 2 should not be hijacked by the dated Season 1 entry
    seasons = [
        media(1, 12, sequel=2, title="Show (2024)"),
        media(2, 12, prequel=1, title="Show Season 2"),
    ]
    client = FakeClient(everything=seasons)
    assert resolve(client, FileInfo("Show", 6, season=2, year=2024)).anime_id == 2


def test_resolve_season_resolves_when_entry_title_has_roman_numeral():
    entry = media(2, 12, title="Show II")
    client = FakeClient(everything=[entry])
    assert resolve(client, FileInfo("Show", 3, season=2)).anime_id == 2


class FolderSeasonClient(FakeClient):
    """Show has 12 + 13 episodes; the season title finds only the second."""

    def search(self, title, year, formats):
        seasons = [media(1, 12, sequel=2, title="Show"), media(2, 13, prequel=1, title="Show 2nd Season")]
        return ([], [seasons[1]]) if title == "Show Season 2" else ([], seasons)


@pytest.mark.parametrize(("episode", "relative"), [(5, 5), (13, 1), (25, 13)])
def test_resolve_folder_season_numbers_are_absolute_when_they_land_past_the_first_season(episode, relative):
    info = resolve(FolderSeasonClient(), FileInfo("Show", episode, season=2, folder_season=True))
    assert (info.anime_id, info.episode) == (2, relative)


@pytest.mark.parametrize(("episode", "relative"), [(5, 5), (13, 13), (25, 13)])
def test_resolve_untitled_file_in_a_season_folder_is_numbered_within_it(episode, relative):
    file = FileInfo("Show", episode, season=2, folder_season=True, untitled=True)
    info = resolve(FolderSeasonClient(), file)
    assert (info.anime_id, info.episode) == (2, relative)


def test_resolve_untitled_episode_in_a_part_folder_is_not_counted_from_the_first_part():
    """ "86 Part 2/E12 - Welcome": 11 + 12 episodes, so E12 counted from Part 1 would be Part 2 E1."""

    class PartClient(FakeClient):
        def search(self, title, year, formats):
            parts = [media(1, 11, sequel=2, title="86"), media(2, 12, prequel=1, title="86 Part 2")]
            return ([], [parts[1]]) if title == "86 Part 2" else ([], parts)

    info = resolve(PartClient(), parse_path("Anime/86 Part 2/E12 - Welcome [F1119374].mkv"))
    assert (info.anime_id, info.episode) == (2, 12)


def test_resolve_episode_without_folder_season_stays_in_the_first_season():
    assert resolve(FolderSeasonClient(), FileInfo("Show", 5)).anime_id == 1


def test_resolve_numbered_films_ranked_last_first_pick_the_film_by_number():
    films = [
        media(3, 1, fmt="MOVIE", prequel=2, title="Show III"),
        media(1, 1, fmt="MOVIE", sequel=2, title="Show I"),
        media(2, 1, fmt="MOVIE", sequel=3, prequel=1, title="Show II"),
    ]
    assert [resolve(FakeClient(everything=films), FileInfo("Show", n)).anime_id for n in (1, 2, 3)] == [1, 2, 3]


def test_resolve_film_named_by_the_file_is_not_counted_from_the_first_film():
    films = [
        media(1, 1, fmt="MOVIE", sequel=2, title="Show I"),
        media(2, 1, fmt="MOVIE", sequel=3, prequel=1, title="Show II"),
        media(3, 1, fmt="MOVIE", prequel=2, title="Show III"),
    ]
    assert [
        resolve(FakeClient(everything=films), FileInfo(f"Show {n}", 1)).anime_id for n in ("I", "II", "III")
    ] == [
        1,
        2,
        3,
    ]


def test_resolve_season_numbering_does_not_go_on_into_a_differently_named_entry():
    seasons = [media(1, 12, sequel=2, title="Owari"), media(2, 4, prequel=1, title="Tsuki")]
    with pytest.raises(SkipUpdate):
        resolve(FakeClient(everything=seasons), FileInfo("Owari", 13, season=1))


def test_resolve_bracketed_name_picks_the_entry_titled_with_it():
    series = media(1, 26, title="Monogatari Second Season")
    series["synonyms"] = ["Nekomonogatari White"]
    black = media(2, 4, title="Nekomonogatari (Kuro)")
    black["title"]["english"] = "Nekomonogatari Black"
    info = resolve(FakeClient(everything=[series, black]), FileInfo("Nekomonogatari", 4, alt_title="Black"))
    assert info.anime_id == 2


class SeriesOnlyClient(FakeClient):
    def search(self, title, year, formats):
        return ([], [media(1, 25, title="Big Show")]) if title == "Big Show" else ([], [])


def test_resolve_numbered_film_is_not_found_by_cutting_its_title_down_to_the_series():
    assert resolve(SeriesOnlyClient(), FileInfo("Big Show Extra Story Chronicle", 1)).anime_id == 1
    with pytest.raises(SkipUpdate):
        resolve(SeriesOnlyClient(), FileInfo("Big Show Movie 04 Chronicle", 1))


class OvaOfTheTitleClient(FakeClient):
    def search(self, title, year, formats):
        return ([], [media(5, 1, fmt="OVA", title="Show")]) if title == "Show" else ([], [])


def test_resolve_extra_in_a_season_folder_is_not_counted_from_the_first_season():
    file = FileInfo("Show", 2, season=6, media_format="OVA", folder_season=True)
    with pytest.raises(SkipUpdate):
        resolve(OvaOfTheTitleClient(), file)


class WithoutBracketedWordClient(FakeClient):
    def search(self, title, year, formats):
        return ([], [media(1, 26, title="Nekomonogatari")]) if title == "Nekomonogatari" else ([], [])


def test_resolve_bracketed_word_the_entry_lacks_is_dropped_when_the_whole_finds_nothing():
    file = FileInfo("Nekomonogatari Shiro", 5, alt_title="Shiro")
    assert resolve(WithoutBracketedWordClient(), file).anime_id == 1


def test_resolve_part_of_a_short_ova_is_its_episode():
    ova = media(1, 2, fmt="OVA", title="Show Side Story")
    client = FakeClient(everything=[ova])
    assert [resolve(client, FileInfo("Show Side Story", 1, part=n)).episode for n in (1, 2)] == [1, 2]
    named = media(2, 2, fmt="OVA", title="Show Side Story Part 2")
    assert resolve(FakeClient(everything=[named]), FileInfo("Show Side Story", 1, part=2)).episode == 1


def test_resolve_file_beyond_its_season_folder_is_not_cut_down_to_the_series():
    file = FileInfo("Big Show Finale 1", 1)
    assert resolve(SeriesOnlyClient(), file).anime_id == 1
    with pytest.raises(SkipUpdate):
        resolve(SeriesOnlyClient(), replace(file, unused_season=True))
