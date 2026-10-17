import json
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path

import pytest

from anilist_updater.errors import ParseError
from anilist_updater.parsing import parse_path
from anilist_updater.release import parse_release

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES = json.loads((FIXTURES_DIR / "filenames.json").read_text(encoding="utf-8"))
# One group per workaround for a guessit quirk, so a failure names the one that broke
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
    assert [info.title, info.episode, info.year, info.media_format] == expected


@pytest.mark.parametrize(("path", "expected"), FIXTURES.items())
def test_parse_path(path, expected):
    check(path, expected)


@pytest.mark.parametrize(("path", "expected"), HACKS)
def test_guessit_workarounds(path, expected):
    check(path, expected)


def test_windows_separators():
    assert parse_path(r"D:\Anime\86 Eighty-Six\E12 - Welcome [F1119374].mkv").title == "86 Eighty Six"


@pytest.mark.parametrize("episode", range(1, 14))
def test_each_file_of_a_series_gets_its_own_episode(episode):
    name = f"[Foobar] Sample Show S2 - {episode:02}v2 (BD 1080p) [ABCD1234].mkv"
    info = parse_path(name)
    assert (info.title, info.episode) == ("Sample Show Season 2", episode)


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
