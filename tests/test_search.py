import io
import json

import pytest

from anilist_updater import cli
from anilist_updater.api import AniListClient


def page(*media):
    return {"Page": {"media": list(media)}}


def media(anime_id, romaji, english=None, entry=None):
    return {
        "id": anime_id,
        "title": {"romaji": romaji, "english": english},
        "format": "TV",
        "seasonYear": 2022,
        "episodes": 24,
        "mediaListEntry": entry,
    }


def test_search_titles_flattens_matches(monkeypatch):
    client = AniListClient(None)
    data = page(media(1, "Aoashi", entry={"status": "CURRENT"}), media(2, None, "Blue Giant"))
    monkeypatch.setattr(client, "_post", lambda _query, _variables: data)

    first, second = client.search_titles("aoashi")
    assert first == {"id": 1, "title": "Aoashi", "format": "TV", "year": 2022, "episodes": 24, "status": "CURRENT"}
    assert (second["title"], second["status"]) == ("Blue Giant", None)


def test_score_action_returns_the_format_and_value(monkeypatch):
    monkeypatch.setattr(AniListClient, "from_token_file", lambda: AniListClient("token"))
    monkeypatch.setattr(AniListClient, "score", lambda _self, _anime_id: ("POINT_10_DECIMAL", 8.5))
    monkeypatch.setattr(cli, "Cache", lambda *_: pytest.fail("score must not load the cache"))

    response = cli.handle({"action": "score", "anime_id": 1})
    assert response["score"] == {"format": "POINT_10_DECIMAL", "value": 8.5}


def test_score_without_a_token_is_a_skip(monkeypatch):
    monkeypatch.setattr(AniListClient, "from_token_file", lambda: AniListClient(None))

    response = cli.handle({"action": "score", "anime_id": 1})
    assert (response["kind"], response["message"]) == ("skip", "Scoring needs an AniList token.")


def test_search_action_returns_results_without_touching_the_cache(monkeypatch):
    monkeypatch.setattr(AniListClient, "from_token_file", lambda: AniListClient(None))
    monkeypatch.setattr(AniListClient, "search_titles", lambda _self, query: [{"id": 1, "title": query}])
    monkeypatch.setattr(cli, "Cache", lambda *_: pytest.fail("search must not load the cache"))

    response = cli.handle({"action": "search", "query": "aoashi"})
    assert response["ok"]
    assert response["results"] == [{"id": 1, "title": "aoashi"}]


def test_invalid_request_is_an_error(monkeypatch):
    stdout = io.BytesIO()
    monkeypatch.setattr(cli.sys, "stdin", type("In", (), {"buffer": io.BytesIO(b"{nope")})())
    monkeypatch.setattr(cli.sys, "stdout", type("Out", (), {"buffer": stdout})())
    cli.main()
    assert json.loads(stdout.getvalue()) == {
        "ok": False,
        "kind": "error",
        "message": "Invalid request.",
        "info": None,
    }
