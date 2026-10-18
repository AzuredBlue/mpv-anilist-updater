import json
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path

import pytest

from anilist_updater.errors import ParseError
from anilist_updater.models import FileInfo
from anilist_updater.parsing import parse_path
from anilist_updater.release import parse_release

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES = json.loads((FIXTURES_DIR / "filenames.json").read_text(encoding="utf-8"))
# One group per quirk of release names, so a failure names the one that broke
HACKS = [
    pytest.param(path, expected, id=f"{hack.split()[0]}-{i}")
    for hack, cases in json.loads((FIXTURES_DIR / "hacks.json").read_text(encoding="utf-8")).items()
    for i, (path, expected) in enumerate(cases.items())
]


def check(path, expected):
    if expected == "error":
        with pytest.raises(ParseError):
            parse_path(path)
        return
    info = parse_path(path)
    assert [info.title, info.episode, info.season, info.part, info.year, info.media_format] == expected


@pytest.mark.parametrize(("path", "expected"), FIXTURES.items())
def test_parse_path(path, expected):
    check(path, expected)


@pytest.mark.parametrize(("path", "expected"), HACKS)
def test_release_name_quirks(path, expected):
    check(path, expected)


def test_titled_filename_takes_no_season_from_its_folder():
    info = parse_path("Anime/Sample Show Season 3/Sample Show - 38.mkv")
    assert (info.title, info.season, info.folder_season) == ("Sample Show", None, False)
    assert parse_path("Anime/Show/Season 3/05.mkv").folder_season
    assert not parse_path("Anime/Sample Show/[Foobar] Sample Show S3 - 05.mkv").folder_season


def test_windows_separators():
    assert parse_path(r"D:\Anime\86 Eighty-Six\E12 - Welcome [F1119374].mkv").title == "86 Eighty Six"


@pytest.mark.parametrize("episode", range(1, 14))
def test_each_file_of_a_series_gets_its_own_episode(episode):
    name = f"[Foobar] Sample Show S2 - {episode:02}v2 (BD 1080p) [ABCD1234].mkv"
    info = parse_path(name)
    assert (info.title, info.season, info.episode) == ("Sample Show", 2, episode)


def test_alternative_titles_pick_the_latin_one():
    release = parse_release("[Foobar] Sample Show /  样本 - 02 [1080P][CHT][MP4]")
    assert (release.title, release.episode) == ("Sample Show", 2)
    release = parse_release("[Foobar] 样本 第二季 / Sample Show (2026) [01][1080P]")
    assert (release.title, release.season, release.year, release.episode) == ("Sample Show", 2, 2026, 1)


def test_guessit_is_not_imported():
    code = (
        "import sys, anilist_updater.parsing as p; p.parse_path('Show - 01.mkv'); print('guessit' in sys.modules)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)  # ruff: ignore[subprocess-without-shell-equals-true]
    assert result.stdout.strip() == "False"


def test_release_parts():
    release = parse_release("[Foobar] Sample Show S2 - 02v2 (BD 1080p) [ABCD1234].mkv")
    assert (release.group, release.crc, release.version, release.resolution, release.source, release.tags) == (
        "Foobar",
        "ABCD1234",
        2,
        "1080p",
        "BD",
        (),
    )
    assert parse_release("Sample.Show.S01E01.1080p.WEB-DL.H.264-GRP.mkv").group == "GRP"
    assert parse_release("Sample Show S01E01 1080p WEB-DL Dual-Audio").group is None
    release = parse_release("[Foobar] Sample Show (Other Name) [Dual Audio] - 01 [1080p]")
    assert (release.alt_titles, release.tags) == (("Other Name",), ("Dual Audio",))


def test_decimal_episode_keeps_the_title_clean():
    release = parse_release("Sample Show - 06.5.mkv")
    assert (release.title, release.episode, release.skip) == ("Sample Show", None, "a special between episodes")


def test_leading_number_keeps_the_text_after_it_as_a_last_resort():
    release = parse_release("03 - Sample Show.mkv")
    assert (release.title, release.episode, release.episode_title) == (None, 3, "Sample Show")
    assert parse_release("E12 - Welcome.mkv").episode_title is None


@pytest.mark.parametrize(
    ("path", "absolute"),
    [
        ("[Foobar] Sample Show S2 [02(14)][Ma10p_1080p][x265_flac].mkv", 14),
        ("[Foobar] Sample Show - S02E12 (063).mkv", 63),
        ("[Foobar] Sample Show - S02E12 (1080p).mkv", None),
        ("[Foobar] Sample Show - S02E12 [1080].mkv", None),
        ("[Foobar] Sample Show - 12 (063).mkv", None),
    ],
)
def test_absolute_number_beside_the_episode(path, absolute):
    assert parse_path(path).absolute == absolute


@pytest.mark.parametrize(
    ("path", "title", "alt_title"),
    [
        ("[Foobar] Sample Show (Other Name) - 03 [1080p].mkv", "Sample Show", "Other Name"),
        (
            "[Foobar] Sample Show - Fujiko Mine`s Lie (Mine Fujiko) [BD].mkv",
            "Sample Show Fujiko Mine's Lie",
            "Mine Fujiko",
        ),
        (
            "[Foobar] Sample Studio - Movie 00 - Wind Valley [BD 1080p].mkv",
            "Sample Studio 00 Wind Valley",
            "Wind Valley",
        ),
        ("[Foobar] Sample Show Movie 01 - First Part - Initiation.mkv", None, "First Part Initiation"),
        (
            "[Foobar] Sample Studio - Movie 04 - Kiki's Service [BD 1080p].mkv",
            "Sample Studio 04 Kiki's Service",
            "Kiki's Service",
        ),
        ("Sample Show - Movie 1-The Legend of Raoh (2006) BD 1080p.mkv", None, "The Legend of Raoh"),
        ("[Foobar] Sample Show - 03 [1080p].mkv", "Sample Show", None),
    ],
)
def test_other_name_of_the_show(path, title, alt_title):
    info = parse_path(path)
    assert info.alt_title == alt_title
    assert title is None or info.title == title


def test_title_stays_plain_and_the_query_adds_season_and_part():
    info = parse_path("[Foobar] Sample Show S2 Part 2 - 05 [1080p].mkv")
    assert (info.title, info.season, info.part) == ("Sample Show", 2, 2)
    assert info.query == "Sample Show Season 2 Part 2"
    assert FileInfo("Sample Show", 1).query == "Sample Show"
    assert FileInfo("Sample Show", 1, season=1).query == "Sample Show"
    assert FileInfo("Sample Show", 1, season=1, part=2).query == "Sample Show Season 1 Part 2"
    assert FileInfo("Sample Show", 1, part=1).query == "Sample Show Part 1"
    assert FileInfo("Sample Show", 1, season=2, part=1).query == "Sample Show Season 2"
    assert FileInfo("Sample Show", 1, season=1, part=1).query == "Sample Show"


@pytest.mark.parametrize(
    ("path", "arc"),
    [
        ("Show/Season 02 (Part 03) - Stardust Crusaders/Show (2012) S02E37.mkv", "Stardust Crusaders"),
        ("Show/Season 2 - Soul Society Arc/Show - S02E03.mkv", "Soul Society Arc"),
        ("Show/Season 2/Show - S02E03.mkv", None),
        ("Show/Season 3 Roberta's Blood Trail/Show - 026.mkv", "Roberta's Blood Trail"),
        ("Show/Season 3/Show - 026.mkv", None),
    ],
)
def test_arc_named_by_the_season_folder(path, arc):
    assert parse_path(path).arc == arc


@pytest.mark.parametrize(
    ("path", "marks"),
    [
        ("[Foobar] Sample Show!! [01][1080p].mkv", "!!"),
        ("[Foobar] Sample Show! - 03 [1080p].mkv", "!"),
        ("[Foobar] Sample Show - 03 [1080p].mkv", ""),
        ("[Foobar] Sample Show - 03! [1080p].mkv", ""),
    ],
)
def test_marks_the_title_ends_with(path, marks):
    assert parse_path(path).marks == marks


@pytest.mark.parametrize(
    ("path", "folder_title"),
    [
        ("[Foobar] 09 - Sample Show Fifth Stage/[Foobar] Sample Show - S05E07.mkv", "Sample Show Fifth Stage"),
        ("Sample Show Collection/Sample Show - 03.mkv", "Sample Show Collection"),
        ("Sample Show/Sample Show - 03.mkv", None),
        ("Sample Showcase/Sample Show - 03.mkv", None),
        ("Other/Sample Show - 03.mkv", None),
    ],
)
def test_folder_title_that_goes_on_from_the_file_title(path, folder_title):
    assert parse_path(path).folder_title == folder_title


def test_number_followed_by_words_stays_in_the_title_when_a_season_episode_follows():
    release = parse_release("[Foobar] Sample Show - 100 Years Quest - S01E01v2.mkv")
    assert (release.title, release.season, release.episode) == ("Sample Show 100 Years Quest", 1, 1)
    assert parse_release("[Foobar] Sample Show - 100 Years Quest - 05.mkv").episode == 5
