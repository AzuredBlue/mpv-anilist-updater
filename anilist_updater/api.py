"""Minimal AniList GraphQL client on the standard library."""

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Self

from .errors import AniListError, SkipUpdate
from .models import Status

log = logging.getLogger(__name__)

API_URL = "https://graphql.anilist.co"
TOKEN_PATH = Path(__file__).resolve().parent.parent / "anilistToken.txt"
TIMEOUT = 10

_MEDIA_FIELDS = """
    fragment MediaFields on Media {
        id
        idMal
        title { romaji english }
        episodes
        duration
        format
        status
        mediaListEntry { status progress }
        relations { edges { relationType node { id format title { romaji } } } }
    }
"""

_SEARCH = (
    _MEDIA_FIELDS
    + """
    query($search: String, $year: FuzzyDateInt, $formats: [MediaFormat]) {
        GlobalSearch: Page(page: 1, perPage: 20) {
            media(search: $search, type: ANIME, startDate_greater: $year, format_in: $formats,
                  status_not: NOT_YET_RELEASED) { ...MediaFields }
        }
        UserSearch: Page(page: 1, perPage: 20) {
            media(search: $search, type: ANIME, startDate_greater: $year, format_in: $formats,
                  status_not: NOT_YET_RELEASED, onList: true) { ...MediaFields }
        }
    }
"""
)

_SEARCH_TITLES = """
    query($search: String) {
        Page(page: 1, perPage: 8) {
            media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
                id
                title { romaji english }
                format
                seasonYear
                episodes
                mediaListEntry { status }
            }
        }
    }
"""

_SAVE_ENTRY = """
    mutation($mediaId: Int, $progress: Int, $status: MediaListStatus) {
        SaveMediaListEntry(mediaId: $mediaId, progress: $progress, status: $status) {
            status
            progress
        }
    }
"""

_MEDIA_BY_ID = """
    query($id: Int) {
        Media(id: $id, type: ANIME) {
            id
            idMal
            title { romaji english }
            episodes
            mediaListEntry { status progress }
        }
    }
"""


def _error_message(error: urllib.error.HTTPError) -> str:
    try:
        message = json.load(error)["errors"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        message = str(error.reason)
    if error.code == 429:
        message += f" (retry in {error.headers.get('Retry-After', '?')}s)"
    return message


class AniListClient:
    """The three AniList operations the updater needs."""

    def __init__(self, token: str | None) -> None:
        """Create a client; without a token only public data is readable."""
        self._token = token

    @classmethod
    def from_token_file(cls, path: Path = TOKEN_PATH) -> Self:
        """Read the token from the first line of `path`."""
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return cls(None)
        return cls(lines[0].strip() if lines else None)

    def _post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        # AniList rejects the default Python-urllib agent with a 403.
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "mpv-anilist-updater",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        body = json.dumps({"query": query, "variables": variables}).encode()
        request = urllib.request.Request(API_URL, body, headers)

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # ruff: ignore[suspicious-url-open-usage]
                payload = json.load(response)
        except urllib.error.HTTPError as e:
            raise AniListError(_error_message(e), e.code) from e
        except OSError as e:
            raise AniListError(f"Couldn't reach AniList: {e}") from e

        if errors := payload.get("errors"):
            raise AniListError(errors[0].get("message", "Unknown error"))
        return payload["data"]

    def search(
        self, title: str, year: int | None, formats: list[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Search the user's list and all of AniList in one request.

        Returns:
            tuple[list[dict[str, Any]], list[dict[str, Any]]]: User list results, then global results.
        """
        variables = {"search": title, "year": year * 10000 if year else 1, "formats": formats}
        data = self._post(_SEARCH, variables)
        return data["UserSearch"]["media"], data["GlobalSearch"]["media"]

    def search_titles(self, query: str) -> list[dict[str, Any]]:
        """Look up anime by title for the correction overlay, in one light request.

        Returns:
            list[dict[str, Any]]: Best matches first, each with `id`, `title`, `format`, `year`, `episodes`
                and the user's list `status` (None if not on the list).
        """
        media = self._post(_SEARCH_TITLES, {"search": query})["Page"]["media"]
        return [
            {
                "id": m["id"],
                "title": m["title"]["romaji"] or m["title"]["english"],
                "format": m["format"],
                "year": m["seasonYear"],
                "episodes": m["episodes"],
                "status": (m["mediaListEntry"] or {}).get("status"),
            }
            for m in media
        ]

    def media_by_id(self, anime_id: int) -> dict[str, Any]:
        """Fetch one entry by AniList ID.

        Raises:
            SkipUpdate: If no such anime exists.
            AniListError: If the request fails.
        """
        try:
            return self._post(_MEDIA_BY_ID, {"id": anime_id})["Media"]
        except AniListError as e:
            if e.status == 404:
                raise SkipUpdate(f"Could not find anime with AniList ID {anime_id}.") from e
            raise

    def save_entry(
        self, anime_id: int, status: Status | None = None, progress: int | None = None
    ) -> dict[str, Any]:
        """Create or update a list entry, sending only what is given.

        Returns:
            dict[str, Any]: The saved entry's `status` and `progress`.

        Raises:
            AniListError: If there is no token, or the request fails.
        """
        if not self._token:
            raise AniListError("No AniList token found. Put it in anilistToken.txt.")
        variables = {"mediaId": anime_id, "status": status, "progress": progress}
        variables = {k: v for k, v in variables.items() if v is not None}
        return self._post(_SAVE_ENTRY, variables)["SaveMediaListEntry"]
