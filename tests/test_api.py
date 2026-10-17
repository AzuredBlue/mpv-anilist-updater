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
