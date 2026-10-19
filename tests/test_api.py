import http.client
import io

import pytest

from anilist_updater import api
from anilist_updater.errors import AniListError


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def post(monkeypatch, response):
    def urlopen(*_args, **_kwargs):
        if isinstance(response, Exception):
            raise response
        return Response(response)

    monkeypatch.setattr(api.urllib.request, "urlopen", urlopen)
    return api.AniListClient("token")._post("query", {})  # ruff: ignore[private-member-access]


def test_post_returns_the_data(monkeypatch):
    assert post(monkeypatch, b'{"data": {"a": 1}}') == {"a": 1}


def test_a_page_that_is_not_json_is_an_anilist_error(monkeypatch):
    with pytest.raises(AniListError, match="unreadable"):
        post(monkeypatch, b"<html>Just a moment...</html>")


def test_a_cut_off_response_is_an_anilist_error(monkeypatch):
    with pytest.raises(AniListError, match="Couldn't reach AniList"):
        post(monkeypatch, http.client.IncompleteRead(b""))


def test_score_reads_format_and_value_in_one_request(monkeypatch):
    calls = []

    def fake_post(_query, variables):
        calls.append(variables)
        return {
            "Viewer": {"mediaListOptions": {"scoreFormat": "POINT_100"}},
            "Media": {"mediaListEntry": {"score": 85}},
        }

    client = api.AniListClient("token")
    monkeypatch.setattr(client, "_post", fake_post)
    assert client.score(1) == ("POINT_100", 85)
    assert len(calls) == 1


def test_prefetch_searches_in_one_request_and_search_answers_from_it(monkeypatch):
    calls = []

    def fake_post(_query, variables):
        calls.append(variables)
        return {f"{page}{i}": {"media": [{"id": i}]} for i in range(3) for page in ("User", "Global")}

    client = api.AniListClient("token")
    monkeypatch.setattr(client, "_post", fake_post)
    client.prefetch([("Show Season 2", 2023), ("Show", 2023), ("Show Season 2", None), ("Show", 2023)], ["TV"])
    assert client.search("Show", 2023, ["TV"]) == ([{"id": 1}], [{"id": 1}])
    assert client.search("Show Season 2", None, ["TV"]) == ([{"id": 2}], [{"id": 2}])
    assert len(calls) == 1
    assert calls[0] == {
        "formats": ["TV"],
        "s0": "Show Season 2",
        "s1": "Show",
        "s2": "Show Season 2",
        "y0": 20230000,
        "y1": 20230000,
        "y2": 1,
    }


def test_search_makes_a_request_only_for_a_search_not_made_yet(monkeypatch):
    calls = []

    def fake_post(_query, variables):
        calls.append(variables)
        return {"User0": {"media": []}, "Global0": {"media": []}}

    client = api.AniListClient("token")
    monkeypatch.setattr(client, "_post", fake_post)
    client.search("Show", None, ["TV"])
    client.search("Show", None, ["TV"])
    client.search("Show", None, ["MOVIE"])
    assert len(calls) == 2
