from anilist_updater.errors import SkipUpdate


def media(
    anime_id,
    episodes,
    *,
    fmt="TV",
    sequel=None,
    prequel=None,
    title=None,
    duration=24,
    status="FINISHED",
    entry=None,
    start=None,
):
    edges = [{"relationType": "SEQUEL", "node": {"id": sequel, "format": "TV"}}] if sequel else []
    edges += [{"relationType": "PREQUEL", "node": {"id": prequel, "format": "TV"}}] if prequel else []
    return {
        "id": anime_id,
        "title": {"romaji": title or f"Show {anime_id}", "english": None},
        "episodes": episodes,
        "duration": duration,
        "format": fmt,
        "status": status,
        "startDate": {"year": start},
        "mediaListEntry": entry,
        "relations": {"edges": edges},
    }


SEASONS = [
    media(1, 12, sequel=2),
    media(2, 24, sequel=3, prequel=1),
    media(3, None, prequel=2, status="RELEASING"),
]


class FakeClient:
    def __init__(self, user=(), everything=(), by_id=None):
        self.user, self.everything, self.by_id = list(user), list(everything), by_id or {}

    def prefetch(self, searches, formats):
        pass

    def search(self, title, year, formats):
        return self.user, self.everything

    def media_by_id(self, anime_id):
        if anime_id not in self.by_id:
            raise SkipUpdate("missing")
        return self.by_id[anime_id]
