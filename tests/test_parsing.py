import json
from pathlib import Path

import pytest

from anilist_updater.errors import ParseError
from anilist_updater.parsing import parse_path

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "filenames.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(("path", "expected"), FIXTURES.items())
def test_parse_path(path, expected):
    if expected == "error":
        with pytest.raises(ParseError):
            parse_path(path)
        return
    info = parse_path(path)
    assert [info.title, info.episode, info.year, info.media_format] == expected


def test_windows_separators():
    assert parse_path(r"D:\Anime\86 Eighty-Six\E12 - Welcome [F1119374].mkv").title == "86 Eighty-Six"
