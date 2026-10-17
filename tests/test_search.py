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


def test_search_action_returns_results_without_touching_the_cache(monkeypatch):
    monkeypatch.setattr(AniListClient, "from_token_file", lambda: AniListClient(None))
    monkeypatch.setattr(AniListClient, "search_titles", lambda _self, query: [{"id": 1, "title": query}])
    monkeypatch.setattr(cli, "Cache", lambda *_: pytest.fail("search must not load the cache"))

    response = cli.handle({"action": "search", "query": "aoashi"})
    assert response["ok"]
    assert response["results"] == [{"id": 1, "title": "aoashi"}]
