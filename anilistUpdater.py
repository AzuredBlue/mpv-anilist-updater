"""
mpv-anilist-updater: Auto-update AniList based on MPV file watching.

Parses anime filenames, finds AniList entries, and updates progress/status.
"""

# Configuration options for anilistUpdater (set in anilistUpdater.conf):
#   DIRECTORIES: List or comma/semicolon-separated string. The directories the script will work on. Leaving it empty will make it work on every video you watch with mpv. Example: DIRECTORIES = ["D:/Torrents", "D:/Anime"]
#   UPDATE_PERCENTAGE: Integer (0-100). The percentage of the video you need to watch before it updates AniList automatically. Default is 85 (usually before the ED of a usual episode duration).
#   SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE: Boolean. If true, when watching episode 1 of a completed anime, set it to rewatching and update progress.
#   UPDATE_PROGRESS_WHEN_REWATCHING: Boolean. If true, allow updating progress for anime set to rewatching. This is for if you want to set anime to rewatching manually, but still update progress automatically.
#   SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT: Boolean. If true, set to COMPLETED after last episode if status was CURRENT.
#   SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING: Boolean. If true, set to COMPLETED after last episode if status was REPEATING (rewatching).
#   ADD_ENTRY_IF_MISSING: Boolean. If true, automatically add anime to your list when an update is triggered (i.e., when you've watched enough of the episode). Default is False.

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════

import hashlib
import json
import os
import re
import sys
import time
import webbrowser
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

import requests
from guessit import guessit  # type: ignore

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════


@dataclass
class SeasonEpisodeInfo:
    """Season and episode info for absolute numbering."""

    season_id: int | None
    season_title: str | None
    progress: int | None
    episodes: int | None
    relative_episode: int | None


@dataclass
class AnimeInfo:
    """Anime information including progress and status."""

    anime_id: int | None
    anime_name: str | None
    current_progress: int | None
    total_episodes: int | None
    file_progress: int | None
    current_status: str | None
    mal_id: int | None = None

    # Can not specify the type further. Causes some of the the variables type checking to be unhappy.
    def __iter__(self) -> Iterator[Any]:  # fmt: off
        """Allow tuple unpacking of AnimeInfo."""
        return iter((self.anime_id, self.anime_name, self.current_progress, self.total_episodes, self.file_progress, self.current_status, self.mal_id))  # fmt: off


@dataclass
class FileInfo:
    """Parsed filename information."""

    name: str
    episode: int
    year: str
    file_format: str | None

    def __iter__(self) -> Iterator[Any]:
        """Allow tuple unpacking of FileInfo."""
        return iter((self.name, self.episode, self.year, self.file_format))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════
# GRAPHQL QUERIES
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════


class AniListQueries:
    """GraphQL queries for AniList API operations."""

    # Query to search for anime with optional filters
    # Variables: search (String), year (FuzzyDateInt), page (Int), format (MediaFormat)
    SEARCH_ANIME = """
        query($search: String, $year: FuzzyDateInt, $page: Int, $format_in: [MediaFormat]) {
            GlobalSearch: Page(page: $page, perPage: 20) {
                media (search: $search, type: ANIME, startDate_greater: $year, format_in: $format_in, status_not:NOT_YET_RELEASED) {
                    id
                    idMal
                    title { romaji, english }
                    season
                    seasonYear
                    episodes
                    duration
                    format
                    status
                    mediaListEntry {
                        status
                        progress
                        media {
                            episodes
                        }
                    }
                    relations {
                        edges {
                            relationType
                            node {
                                id
                                format
                                title {
                                    romaji
                                }
                            }
                        }
                    }
                }
            }

            UserSearch: Page(page: $page, perPage: 20) {
                media (search: $search, type: ANIME, startDate_greater: $year, format_in: $format_in, status_not:NOT_YET_RELEASED, onList: true) {
                    id
                    idMal
                    title { romaji, english }
                    season
                    seasonYear
                    episodes
                    duration
                    format
                    status
                    startDate { year month day }
                    mediaListEntry {
                        status
                        progress
                        media {
                            episodes
                        }
                    }
                    relations {
                        edges {
                            relationType
                            node {
                                id
                                format
                                title {
                                    romaji
                                }
                            }
                        }
                    }
                }
            }
        }
    """

    # Mutation to save/update media list entry (works for both adding and updating)
    # Variables: mediaId (Int), progress (Int), status (MediaListStatus)
    SAVE_MEDIA_LIST_ENTRY = """
        mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
            SaveMediaListEntry (mediaId: $mediaId, progress: $progress, status: $status) {
                status
                id
                progress
                mediaId
            }
        }
    """

    # Query to get anime by AniList ID
    GET_ANIME_BY_ID = """
        query($id: Int) {
            Media(id: $id, type: ANIME) {
                id
                idMal
                title { romaji, english }
                episodes
                mediaListEntry {
                    status
                    progress
                }
            }
        }
    """


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════
# MAIN ANILIST UPDATER CLASS
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════


class AniListUpdater:
    """AniList authentication, file parsing, API requests, and progress updates."""

    ANILIST_API_URL: str = "https://graphql.anilist.co"
    TOKEN_PATH: str = os.path.join(os.path.dirname(__file__), "anilistToken.txt")
    CACHE_PATH: str = os.path.join(os.path.dirname(__file__), "cache.json")
    OPTIONS: ClassVar[dict[str, Any]] = {"excludes": ["country", "language"]}
    CACHE_REFRESH_RATE: int = 24 * 60 * 60
    CORRECTED_CACHE_REFRESH_RATE: int = 28 * 24 * 60 * 60

    _CHARS_TO_REPLACE: str = r'\/:!*?"<>|._-'
    # Matches any of the chars, only if not followed by a whitespace and a digit.
    CLEAN_PATTERN: str = rf"(?: - Movie)|[{re.escape(_CHARS_TO_REPLACE)}](?!\s*\d)"
    VERSION_REGEX: re.Pattern[str] = re.compile(r"(E\d+)v\d")

    # ──────────────────────────────────────────────────────────────────────────────────────────────────
    # INITIALIZATION & TOKEN HANDLING
    # ──────────────────────────────────────────────────────────────────────────────────────────────────

    # Load token
    def __init__(self, options: dict[str, Any], action: str) -> None:
        """
        Initialize AniListUpdater with configuration and action.

        Args:
            options (dict[str, Any]): Configuration options.
            action (str): Action to perform ('update' or 'launch').
        """
        self.access_token: str | None = self._load_access_token()
        self.options: dict[str, Any] = options
        self.ACTION: str = action
        self._cache: dict[str, Any] | None = None

    # Load token from anilistToken.txt
    def _load_access_token(self) -> str | None:
        """
        Load access token from file.

        Returns:
            str | None: Access token or None if not found.
        """
        try:
            if not os.path.exists(self.TOKEN_PATH):
                return None
            with open(self.TOKEN_PATH, encoding="utf-8") as f:
                lines = f.read().splitlines()
            if not lines:
                return None

            # The first line should have the token.
            return lines[0].strip()

        except Exception as e:
            print(f"Error reading access token: {e}")
            return None

    # ──────────────────────────────────────────────────────────────────────────────────────────────────
    # CACHE MANAGEMENT
    # ──────────────────────────────────────────────────────────────────────────────────────────────────

    def _hash_path(self, path: str) -> str:
        """
        Generate SHA256 hash of path.

        Args:
            path (str): Path to hash.

        Returns:
            str: Hashed path.
        """
        return hashlib.sha256(path.encode("utf-8")).hexdigest()

    def cache_to_file(self, path: str, guessed_name: str, absolute_progress: int, result: AnimeInfo) -> None:
        """
        Store/update cache entry for anime information.

        Args:
            path (str): File path.
            guessed_name (str): Guessed anime name.
            absolute_progress (int): Absolute episode number.
            result (AnimeInfo): Anime information to cache.
        """
        try:
            dir_hash = self._hash_path(os.path.dirname(path))
            cache = self.load_cache()
            existing_entry = cache.get(dir_hash, {})
            is_corrected = bool(existing_entry.get("corrected", False))

            anime_id, _, current_progress, total_episodes, relative_progress, current_status, mal_id = result

            now = time.time()
            ttl_refresh_rate = self.CORRECTED_CACHE_REFRESH_RATE if is_corrected else self.CACHE_REFRESH_RATE

            cache[dir_hash] = {
                "guessed_name": guessed_name,
                "anime_id": anime_id,
                "mal_id": mal_id,
                "current_progress": current_progress,
                "relative_progress": f"{absolute_progress}->{relative_progress}",
                "total_episodes": total_episodes,
                "current_status": current_status,
                "corrected": is_corrected,
                "ttl": now + ttl_refresh_rate,
            }

            self.save_cache(cache)
        except Exception as e:
            print(f"Error trying to cache {result}: {e}")

    def check_and_clean_cache(self, path: str, guessed_name: str) -> dict[str, Any] | None:
        """
        Get valid cache entry and clean expired entries.

        Args:
            path (str): Path to media file.
            guessed_name (str): Guessed anime name.

        Returns:
            dict[str, Any] | None: Cache entry or None if not found/valid.
        """
        try:
            cache = self.load_cache()
            now = time.time()
            changed = False
            # Purge expired
            for k, v in list(cache.items()):
                if v.get("ttl", 0) < now:
                    cache.pop(k, None)
                    changed = True

            dir_hash = self._hash_path(os.path.dirname(path))
            entry = cache.get(dir_hash)

            if entry and entry.get("guessed_name") == guessed_name:
                # Apply sliding expiration only for corrected entries when close to expiration.
                if entry.get("corrected", False) and entry.get("ttl", 0) <= now + (
                    self.CORRECTED_CACHE_REFRESH_RATE // 2
                ):
                    entry["ttl"] = now + self.CORRECTED_CACHE_REFRESH_RATE
                    cache[dir_hash] = entry
                    changed = True

                if changed:
                    self.save_cache(cache)

                return entry

            if changed:
                self.save_cache(cache)

            return None
        except Exception as e:
            print(f"Error trying to read cache file: {e}")
            return None

    def load_cache(self) -> dict[str, Any]:
        """
        Load cache from JSON file with lazy loading.

        Returns:
            dict[str, Any]: Cache data or empty dict if file doesn't exist.
        """
        if self._cache is None:
            try:
                if not os.path.exists(self.CACHE_PATH):
                    self._cache = {}
                else:
                    with open(self.CACHE_PATH, encoding="utf-8") as f:
                        self._cache = json.load(f)
            except Exception:
                self._cache = {}
        # At this point, self._cache is guaranteed to be a dict
        assert self._cache is not None
        return self._cache

    def save_cache(self, cache: dict[str, Any]) -> None:
        """
        Save cache dictionary to JSON file.

        Args:
            cache (dict[str, Any]): Cache data to save.
        """
        try:
            with open(self.CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            # Keep local cache in sync
            self._cache = cache
        except Exception as e:
            print(f"Failed saving cache.json: {e}")

    # ──────────────────────────────────────────────────────────────────────────────────────────────────
    # API COMMUNICATION
    # ──────────────────────────────────────────────────────────────────────────────────────────────────

    # Function to make an api request to AniList's api
    def _make_api_request(
        self, query: str, variables: dict[str, Any] | None = None, access_token: str | None = None
    ) -> dict[str, Any]:
        """
        Make POST request to AniList GraphQL API.

        Args:
            query (str): GraphQL query string.
            variables (dict[str, Any] | None): Query variables.
            access_token (str | None): AniList access token.

        Returns:
            dict[str, Any]: API response data.

        Raises:
            Exception: If the API responds with a non-200 status code.
        """
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        response = requests.post(
            self.ANILIST_API_URL, json={"query": query, "variables": variables}, headers=headers, timeout=10
        )

        response_json = response.json()

        if response.status_code == 200:
            return response_json

        error_msg = response_json.get("errors", [{}])[0].get("message", "Unknown error")
        raise Exception(f"API request failed: {response.status_code} - {error_msg}")

    # ──────────────────────────────────────────────────────────────────────────────────────────────────
    # SEASON & EPISODE HANDLING
    # ──────────────────────────────────────────────────────────────────────────────────────────────────

    # Finds the season and episode of an anime with absolute numbering
    def find_season_and_episode(
        self, seasons: list[dict[str, Any]] | None, absolute_episode: int
    ) -> SeasonEpisodeInfo:
        """
        Find correct season and relative episode for absolute episode number.

        Args:
            seasons (list[dict[str, Any]]): Season dicts.
            absolute_episode (int): Absolute episode number.

        Returns:
            SeasonEpisodeInfo: Season and episode information.
        """
        if seasons is None:
            return SeasonEpisodeInfo(None, None, None, None, None)

        accumulated_episodes = 0
        for season in seasons:
            season_episodes = season.get("episodes", 12) if season.get("episodes") else 12

            if accumulated_episodes + season_episodes >= absolute_episode:
                return SeasonEpisodeInfo(
                    season.get("id"),
                    season.get("title", {}).get("romaji"),
                    season.get("mediaListEntry", {}).get("progress") if season.get("mediaListEntry") else None,
                    episodes=season.get("episodes"),
                    relative_episode=absolute_episode - accumulated_episodes,
                )
            accumulated_episodes += season_episodes
        return SeasonEpisodeInfo(None, None, None, None, None)

    # ──────────────────────────────────────────────────────────────────────────────────────────────────
    # FILE PROCESSING & PARSING
    # ──────────────────────────────────────────────────────────────────────────────────────────────────

    def handle_filename(self, filename: str) -> None:
        """
        Handle file processing: parse, check cache, update AniList.

        Args:
            filename (str): Path to video file.
        """
        file_info = self.parse_filename(filename)
        cache_entry = self.check_and_clean_cache(filename, file_info.name)
        result = None

        # If launching and cache has anime_id, we can skip search and open directly.
        if self.ACTION == "launch" and cache_entry and cache_entry.get("anime_id"):
            open_anilist(file_info.name, cache_entry["anime_id"])
            return

        # Use cached data if available, otherwise fetch fresh info
        if cache_entry:
            left, right = cache_entry.get("relative_progress", "0->0").split("->")
            # For example, if 19->7, that means that 19 absolute is equivalent to 7 relative to this season
            # File episode 20: 20 - 19 + 7 = 8 relative to this season
            offset = int(left) - int(right)

            # Does it make sense if the offset is negative?
            relative_episode = file_info.episode - offset

            # For shows without a total episode count, just assume its correct as long as >= 1
            if 1 <= relative_episode <= (cache_entry.get("total_episodes") or 999):
                print(f'Using cached data for "{file_info.name}": {cache_entry["anime_id"]}')

                # Reconstruct result from cache
                result = AnimeInfo(
                    cache_entry["anime_id"],
                    cache_entry["guessed_name"],
                    cache_entry["current_progress"],
                    cache_entry["total_episodes"],
                    relative_episode,
                    cache_entry["current_status"],
                    cache_entry.get("mal_id"),
                )

        # At this point, we guess using the guessed name and other information
        if result is None:
            result = self.get_anime_info_and_progress(file_info)

        # For "info" action, dumps the JSON with the anime information, save it to cache and return without updating AniList.
        if self.ACTION == "info":
            if result:
                payload = {
                    "anime_id": result.anime_id,
                    "mal_id": result.mal_id,
                    "anime_name": result.anime_name,
                    "episode": result.file_progress,
                    "current_progress": result.current_progress,
                    "total_episodes": result.total_episodes,
                    "current_status": result.current_status,
                }
                print(f"INFO:{json.dumps(payload)}")
                if result.current_progress is not None:
                    self.cache_to_file(filename, file_info.name, file_info.episode, result)
            return

        # total_episodes can be None at first for ongoing anime. It is only necessary to refresh that information
        # if it's a "corrected" anime, since that won't expire.
        should_refresh_missing_episodes = bool(
            self.ACTION == "update"
            and cache_entry
            and cache_entry.get("corrected", False)
            and result
            and result.total_episodes is None
        )
        if should_refresh_missing_episodes:
            result = self.refresh_anime_info_by_id(result)

        result = self.update_episode_count(result)

        if result and result.current_progress is not None:
            # Update cache with latest data
            self.cache_to_file(filename, file_info.name, file_info.episode, result)
        return

    # Attempt to improve detection
    def fix_filename(self, path_parts: list[str]) -> list[str]:
        """
        Apply hardcoded fixes to filename/folder structure for better detection.

        Args:
            path_parts (list[str]): Path components.

        Returns:
            list[str]: Modified path components.
        """
        # Before using guessit, clean up the filename
        path_parts[-1] = re.sub(self.CLEAN_PATTERN, " ", path_parts[-1])
        path_parts[-1] = " ".join(path_parts[-1].split())

        # Remove 'v2', 'v3'... from the title since it fucks up with episode detection
        match = self.VERSION_REGEX.search(path_parts[-1])
        if match:
            episode = match.group(1)
            path_parts[-1] = path_parts[-1].replace(match.group(0), episode)

        return path_parts

    # Parse the file name using guessit
    def parse_filename(self, filepath: str) -> FileInfo:
        """
        Parse filename/folder structure to extract anime info.

        Args:
            filepath (str): Path to video file.

        Returns:
            FileInfo: Parsed info with name, episode, year.

        Raises:
            Exception: If no title is found from file name and the folders it is in.
        """
        path_parts = self.fix_filename(filepath.replace("\\", "/").split("/"))
        filename = path_parts[-1]
        guessed_name, season, part, year = "", "", "", ""
        remaining: list[int] = []
        # First, try to guess from the filename
        guess = guessit(filename, self.OPTIONS)

        print(f"File name guess: {filename} -> {dict(guess)}")

        # Episode guess from the title.
        # Usually, releases are formated [Release Group] Title - S01EX

        # If the episode index is 0, that would mean that the episode is before the title in the filename
        # Which is a horrible way of formatting it, so assume its wrong

        # If its 1, then the title is probably 0, so its okay. (Unless season is 0)
        # Really? What is the format "S1E1 - {title}"? That's almost psycopathic.

        # If its >2, theres probably a Release Group and Title / Season / Part, so its good

        episode = guess.get("episode", None)
        season = guess.get("season", "")
        part = str(guess.get("part", ""))
        year = str(guess.get("year", ""))
        file_format = None

        # Right now, only detect both these formats
        other = guess.get("other", "")
        if other == "Original Animated Video":
            file_format = "OVA"
        elif other == "Original Net Animation":
            file_format = "ONA"

        # Quick fixes assuming season before episode
        # 'episode_title': '02' in 'S2 02'
        if guess.get("episode_title", "").isdigit() and "episode" not in guess:
            print(f"Detected episode in episode_title. Episode: {int(guess.get('episode_title'))}")
            episode = int(guess.get("episode_title"))

        # 'episode': [86, 13] (EIGHTY-SIX), [1, 2, 3] (RANMA) lol.
        if isinstance(episode, list):
            print(f"Detected multiple episodes: {episode}. Picking last one.")
            remaining = episode[:-1]
            episode = episode[-1]

        # 'season': [2, 3] in "S2 03"
        if isinstance(season, list):
            print(f"Detected multiple seasons: {season}. Picking first one as season.")
            # If episode wasn't detected or is default, try to extract from season list
            if episode is None and len(season) > 1:
                print("Episode not detected. Picking last position of the season list.")
                episode = season[-1]

            season = season[0]

        # Ensure episode is never None
        episode = episode or 1

        season = str(season)

        keys = list(guess.keys())
        episode_index = keys.index("episode") if "episode" in guess else 1
        season_index = keys.index("season") if "season" in guess else -1
        title_in_filename = "title" in guess and (episode_index > 0 and (season_index > 0 or season_index == -1))
        found_title = title_in_filename

        # If the title is not in the filename or episode index is 0, try the folder name
        # If the episode index > 0 and season index > 0, its safe to assume that the title is in the file name

        if title_in_filename:
            guessed_name = guess["title"]
        else:
            # If it isnt in the name of the file, try to guess using the name of the folder it is stored in

            # Depth=2 folders
            for depth in [2, 3]:
                folder_guess = guessit(path_parts[-depth], self.OPTIONS) if len(path_parts) > depth - 1 else None
                if folder_guess:
                    print(
                        f"{depth - 1}{'st' if depth - 1 == 1 else 'nd'} Folder guess:\n{path_parts[-depth]} -> {dict(folder_guess)}"
                    )

                    guessed_name = str(folder_guess.get("title", ""))
                    season = season or str(folder_guess.get("season", ""))
                    part = part or str(folder_guess.get("part", ""))
                    year = year or str(folder_guess.get("year", ""))

                    # If we got the name, its probable we already got season and part from the way folders are usually structured
                    if guessed_name:
                        found_title = True
                        break

        if not found_title:
            raise Exception(f"Couldn't find title in filename '{filename}'! Guess result: {guess}")

        # Haven't tested enough but seems to work fine
        # If there are remaining episodes, append them to the name
        if remaining:
            guessed_name += " " + " ".join(str(ep) for ep in remaining)

        # Add season and part if there are
        if season and (int(season) > 1 or part):
            guessed_name += f" Season {season}"

        # Rare case where "Part" is in the episode title: "My Hero Academia S06E06 Encounter, Part 2"
        # If episode_title is detected, part must be before it
        episode_title_index = keys.index("episode_title") if "episode_title" in guess else 99

        if part and keys.index("part") < episode_title_index:
            guessed_name += f" Part {part}"

        print(f"Guessed: {guessed_name}{f' {file_format}' if file_format else ''} - E{episode} {year}")
        return FileInfo(guessed_name, episode, year, file_format)

    # ──────────────────────────────────────────────────────────────────────────────────────────────────
    # ANIME INFO & PROGRESS UPDATES
    # ──────────────────────────────────────────────────────────────────────────────────────────────────

    def filter_valid_seasons(self, seasons: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """
        Filter and sort valid seasons.

        Args:
            seasons (list[dict[str, Any]]): Season dicts from AniList API.

        Returns:
            list[dict[str, Any]] | None: Filtered and sorted seasons or None if no seasons could be found.
        """
        valid_formats = {"TV", "ONA"}

        # Build a list of the main series using relationType, assuming [0] is the correct series
        season_map = {s["id"]: s for s in seasons}

        # Use the first TV | ONA, with duration > 21 as a starting point
        current_node = None
        for s in seasons:
            if s["format"] in valid_formats and (
                (s["duration"] is None and s["status"] == "RELEASING")
                or (s["duration"] is not None and s["duration"] > 21)
            ):
                current_node = s
                break

        if current_node is None:
            return None

        main_series = [current_node]
        visited_ids = {current_node["id"]}

        while True:
            edges = current_node.get("relations", {}).get("edges", [])

            # It might have more than 1 sequel, check for the ones in "season".
            # As "season" might be the user's list, a sequel might not be in season_map
            candidate_sequels = [
                edge["node"]
                for edge in edges
                if edge["relationType"] == "SEQUEL" and edge["node"]["id"] in season_map
            ]

            if not candidate_sequels:
                break

            # At this point, candidate_sequels contain sequels in season_map. Prioritise those that are "TV" or "ONA"
            next_sequel = max(candidate_sequels, key=lambda x: x["format"] in valid_formats)
            next_id = next_sequel["id"]

            if next_id in visited_ids:
                break

            visited_ids.add(next_id)
            current_node = season_map[next_id]

            if current_node["format"] in valid_formats:
                main_series.append(current_node)

        return main_series

    def get_anime_info_and_progress(self, file_info: FileInfo) -> AnimeInfo:
        """
        Query AniList for anime info and user progress.

        Args:
            file_info (FileInfo): Anime file information.

        Returns:
            AnimeInfo: Complete anime information.

        Raises:
            Exception: If it could not find the anime.
        """
        # Unpack file info
        name, file_progress, year, file_format = file_info

        # If theres a format specified, search only for that format.
        format_in = [file_format] if file_format else ["TV", "TV_SHORT", "MOVIE", "SPECIAL", "OVA", "ONA"]

        # We first need to make sure if we should search ALL of anime or only the user's list
        # Only those that are in the user's list at first
        query = AniListQueries.SEARCH_ANIME
        variables = {"search": name, "year": year or 1, "page": 1, "format_in": format_in}

        response = self._make_api_request(query, variables, self.access_token)

        user_list_seasons = response["data"]["UserSearch"]["media"]
        global_search_seasons = response["data"]["GlobalSearch"]["media"]

        # If no results for both, raise exception
        if not user_list_seasons and not global_search_seasons:
            raise Exception(f"Couldn't find an anime from this title! ({name}). Is it in your list?")

        seasons = user_list_seasons or global_search_seasons  # Priority to the user list

        # Results from the API request from the user's list or from global search.
        # If from global search then entry will be None, and the anime will be added if ADD_ENTRY_IF_MISSING
        entry = seasons[0]["mediaListEntry"]
        anime_data = AnimeInfo(
            seasons[0]["id"],
            seasons[0]["title"]["romaji"],
            entry["progress"] if entry is not None else None,
            seasons[0]["episodes"],
            file_progress,
            entry["status"] if entry is not None else None,
            seasons[0]["idMal"],
        )

        is_absolute_numbering = seasons and seasons[0]["episodes"] and file_progress > seasons[0]["episodes"]
        filtered_seasons = []

        # Check if it's using absolute numbering, if so, find out the main series and all sequels
        if is_absolute_numbering:
            filtered_seasons = self.filter_valid_seasons(seasons)
            season_episode_info = self.find_season_and_episode(filtered_seasons, file_progress)

            # If it is None, needs to use global searchto find out the series exact episode
            if not filtered_seasons or season_episode_info.season_id is None:
                seasons = global_search_seasons

                # At this point it should either have the correct main series or it failed
                # Recalculate both
                filtered_seasons = self.filter_valid_seasons(seasons)
                season_episode_info = self.find_season_and_episode(filtered_seasons, file_progress)

                if filtered_seasons is None or season_episode_info.season_id is None:
                    raise Exception(f"No valid seasons found for '{name}'.")

            seasons = filtered_seasons

            found_season = next(
                (season for season in seasons if season["id"] == season_episode_info.season_id), None
            )
            found_entry = (
                found_season["mediaListEntry"] if found_season and found_season["mediaListEntry"] else None
            )
            anime_data = AnimeInfo(
                season_episode_info.season_id,
                season_episode_info.season_title,
                season_episode_info.progress,
                season_episode_info.episodes,
                season_episode_info.relative_episode,
                found_entry["status"] if found_entry else None,
                found_season.get("idMal") if found_season else None,
            )
            print(f"Final guessed anime: {anime_data.anime_name}")
            print(f"Absolute episode {file_progress} corresponds to episode: {anime_data.file_progress}")
        else:
            print(f"Final guessed anime: {seasons[0]['title']['romaji']}")
        return anime_data

    # Update the anime based on file progress
    def update_episode_count(self, result: AnimeInfo) -> AnimeInfo:
        """
        Update episode count and/or status on AniList per user settings.

        Args:
            result (AnimeInfo): Anime information.

        Returns:
            AnimeInfo: Updated anime information.

        Raises:
            Exception: If the update fails.
        """
        if result is None:
            raise Exception("Parameter in update_episode_count is null.")

        anime_id, anime_name, current_progress, total_episodes, file_progress, current_status, mal_id = result

        if anime_id is None:
            raise Exception("Couldn't find that anime! Make sure it is on your list and the title is correct.")

        # Only launch anilist
        if self.ACTION == "launch":
            open_anilist(anime_name, anime_id)
            return result

        should_add_entry = current_progress is None and current_status is None
        is_last_episode = file_progress == total_episodes

        # Handle adding anime to list if it's not already there (ADD_ENTRY_IF_MISSING feature)
        if should_add_entry:
            if not self.options.get("ADD_ENTRY_IF_MISSING", False):
                raise Exception("Failed to get current episode count. Is it on your list?")

            print(f'Adding "{anime_name}" to your list since you\'re watching it...')
            initial_status = "CURRENT"

            if is_last_episode and self.options.get("SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT", False):
                initial_status = "COMPLETED"

            if not self._save_media_list_entry(anime_id, initial_status, file_progress):
                raise Exception(f"Failed to add '{anime_name}' to your list.")

            osd_message(f'Added "{anime_name}" to your list with progress: {file_progress}')

            return AnimeInfo(
                anime_id, anime_name, file_progress, total_episodes, file_progress, initial_status, mal_id
            )

        should_set_to_rewatching = (
            current_status == "COMPLETED"
            and file_progress == 1
            and self.options.get("SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE", False)
        )

        # Handle completed -> rewatching on first episode
        if should_set_to_rewatching:
            # Needs to update in 2 steps, since AniList
            # doesn't allow setting progress while changing the status from completed to rewatching.
            # If you try, it will just reset the progress to 0.
            print("Setting status to REPEATING")

            # Step 1: Set to REPEATING, progress=0
            self._save_media_list_entry(anime_id, "REPEATING", 0)

            # Step 2: Set progress to 1
            response = self._save_media_list_entry(anime_id, None, 1)

            updated_progress = response["data"]["SaveMediaListEntry"]["progress"]
            osd_message(f'Updated "{anime_name}" to REPEATING with progress: {updated_progress}')

            return AnimeInfo(anime_id, anime_name, updated_progress, total_episodes, 1, "REPEATING", mal_id)

        rewatching_and_updating = current_status == "REPEATING" and self.options["UPDATE_PROGRESS_WHEN_REWATCHING"]
        in_modifiable_state = current_status in {"CURRENT", "PLANNING", "PAUSED"}

        # Handle updating progress for rewatching
        if rewatching_and_updating:
            print("Updating progress for anime set to REPEATING.")
            status_to_set = "REPEATING"

        # Only update if status is CURRENT, PLANNING, or PAUSED
        elif in_modifiable_state:
            # If its lower than the current progress, dont update.
            if file_progress and current_progress is not None and file_progress <= current_progress:
                raise Exception(f"Episode was not new. Not updating ({file_progress} <= {current_progress})")

            status_to_set = "CURRENT"

        else:
            raise Exception(f"Anime is not in a modifiable state (status: {current_status}). Not updating.")

        should_set_to_completed = is_last_episode and (
            (in_modifiable_state and self.options["SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT"])
            or (current_status == "REPEATING" and self.options["SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING"])
        )

        if should_set_to_completed:
            status_to_set = "COMPLETED"

        if status_to_set:
            response = self._save_media_list_entry(anime_id, status_to_set, file_progress)
        else:
            response = self._save_media_list_entry(anime_id, None, file_progress)

        updated_progress = response["data"]["SaveMediaListEntry"]["progress"]
        updated_status = response["data"]["SaveMediaListEntry"]["status"]
        osd_message(f'Updated "{anime_name}" to: {updated_progress}')

        return AnimeInfo(
            anime_id, anime_name, updated_progress, total_episodes, file_progress, updated_status, mal_id
        )

    def _save_media_list_entry(self, anime_id: int, status: str | None, progress: int | None) -> dict[str, Any]:
        """
        Helper function to save media list entry.

        Args:
            anime_id (int): AniList anime ID.
            status (str | None): Status to set.
            progress (int | None): Progress to set.

        Returns:
            dict[str, Any]: API response.

        Raises:
            ValueError: If both status and progress are omitted.
        """
        query = AniListQueries.SAVE_MEDIA_LIST_ENTRY
        variables: dict[str, Any] = {"mediaId": anime_id}

        if status is not None:
            variables["status"] = status
        if progress is not None:
            variables["progress"] = progress

        if "status" not in variables and "progress" not in variables:
            raise ValueError("At least one of status or progress must be provided.")

        return self._make_api_request(query, variables, self.access_token)

    def refresh_anime_info_by_id(self, result: AnimeInfo) -> AnimeInfo:
        """
        Refresh anime info by AniList ID.

        Needed for refreshing total_episodes, which will be None for new shows.

        Args:
            result (AnimeInfo): Current anime information.

        Returns:
            AnimeInfo: Updated anime information if found, otherwise original info.
        """
        if result.anime_id is None:
            return result

        query = AniListQueries.GET_ANIME_BY_ID
        variables = {"id": result.anime_id}

        response = self._make_api_request(query, variables, self.access_token)
        media = response.get("data", {}).get("Media")

        if not media:
            return result

        entry = media.get("mediaListEntry")
        refreshed_name = (
            media.get("title", {}).get("romaji") or media.get("title", {}).get("english") or result.anime_name
        )
        refreshed_progress = entry.get("progress") if entry else result.current_progress
        refreshed_status = entry.get("status") if entry else result.current_status

        return AnimeInfo(
            result.anime_id,
            refreshed_name,
            refreshed_progress,
            media.get("episodes"),
            result.file_progress,
            refreshed_status,
            media.get("idMal") or result.mal_id,
        )

    def _correct_anime_id_change(
        self,
        anilist_id: int,
        fallback_name: str,
        fallback_progress: int | None,
        fallback_status: str | None,
    ) -> dict[str, Any]:
        """
        Fetch AniList media data for a changed ID and merge with fallback cache values.

        Raises:
            Exception: If the anime could not be found on AniList.
        """
        query = AniListQueries.GET_ANIME_BY_ID
        variables = {"id": anilist_id}
        response = self._make_api_request(query, variables, self.access_token)

        media = response.get("data", {}).get("Media")
        if not media:
            raise Exception(f"Could not find anime with AniList ID {anilist_id}.")

        entry = media.get("mediaListEntry")
        anime_name = media.get("title", {}).get("romaji") or media.get("title", {}).get("english") or fallback_name

        return {
            "anime_name": anime_name,
            "mal_id": media.get("idMal"),
            "total_episodes": media.get("episodes"),
            "current_progress": entry.get("progress") if entry else fallback_progress,
            "current_status": entry.get("status") if entry else fallback_status,
        }

    def _correct_relative_episode(
        self,
        absolute_episode: int,
        existing_relative_mapping: str | None,
        requested_relative_episode: int | None,
    ) -> tuple[int, bool]:
        """Resolve the mapped relative episode and whether it changed."""
        existing_relative_episode = absolute_episode
        if isinstance(existing_relative_mapping, str):
            _, _, right = existing_relative_mapping.partition("->")
            if right:
                try:
                    existing_relative_episode = int(right)
                except Exception:
                    existing_relative_episode = absolute_episode

        mapped_relative_episode = (
            requested_relative_episode
            if requested_relative_episode and requested_relative_episode > 0
            else existing_relative_episode
        )
        mapped_relative_episode = max(1, mapped_relative_episode)

        return mapped_relative_episode, mapped_relative_episode != existing_relative_episode

    def _correct_status(
        self, anime_id: int, selected_status: str | None, current_status: str | None
    ) -> tuple[str | None, bool]:
        """Update AniList status only when a new status was selected."""
        if not selected_status or selected_status == current_status:
            return current_status, False

        self._save_media_list_entry(anime_id, selected_status, None)
        return selected_status, True

    def _correct_cache(self, cache: dict[str, Any], dir_hash: str, payload: dict[str, Any]) -> None:
        """Persist corrected mapping into cache.json."""
        cache[dir_hash] = payload
        self.save_cache(cache)

    def correct_anime_id(
        self, filepath: str, anilist_id: int, relative_episode: int | None = None, target_status: str | None = None
    ) -> None:
        """
        Correct the anime ID in cache by querying AniList for the given ID.

        Args:
            filepath (str): Path to the currently playing file (used to compute dir hash).
            anilist_id (int): The correct AniList anime ID.
            relative_episode (int | None): Optional relative episode override for current file.
            target_status (str | None): Optional MediaList status override.

        """
        selected_status = target_status.upper() if target_status else None

        file_info = self.parse_filename(filepath)
        dir_hash = self._hash_path(os.path.dirname(filepath))
        cache = self.load_cache()
        existing_entry = cache.get(dir_hash, {})

        guessed_name = existing_entry.get("guessed_name", file_info.name)
        existing_anime_id = existing_entry.get("anime_id")
        id_changed = existing_anime_id != anilist_id

        anime_name = guessed_name
        mal_id = existing_entry.get("mal_id")
        total_episodes = existing_entry.get("total_episodes")
        current_progress = existing_entry.get("current_progress")
        current_status = existing_entry.get("current_status")

        if id_changed:
            id_data = self._correct_anime_id_change(
                anilist_id,
                guessed_name,
                current_progress,
                current_status,
            )
            anime_name = id_data["anime_name"]
            mal_id = id_data["mal_id"]
            total_episodes = id_data["total_episodes"]
            current_progress = id_data["current_progress"]
            current_status = id_data["current_status"]

        mapped_relative_episode, relative_changed = self._correct_relative_episode(
            file_info.episode,
            existing_entry.get("relative_progress"),
            relative_episode,
        )

        status_before = current_status
        current_status, status_changed = self._correct_status(
            anilist_id,
            selected_status,
            current_status,
        )

        changes = [
            f"ID: {existing_anime_id or '?'}->{anilist_id}" if id_changed else None,
            f"Mapped {file_info.episode}->{mapped_relative_episode}" if relative_changed else None,
            f"Status: {status_before or 'None'}->{current_status}" if status_changed else None,
        ]
        changes = [change for change in changes if change]

        if not changes:
            osd_message("No correction changes detected.")
            return

        self._correct_cache(
            cache,
            dir_hash,
            {
                "guessed_name": guessed_name,
                "anime_id": anilist_id,
                "mal_id": mal_id,
                "current_progress": current_progress,
                "relative_progress": f"{file_info.episode}->{mapped_relative_episode}",
                "total_episodes": total_episodes,
                "current_status": current_status,
                "corrected": True if id_changed else existing_entry.get("corrected", False),
                "ttl": time.time() + self.CORRECTED_CACHE_REFRESH_RATE,
            },
        )

        osd_message(f'Corrected "{anime_name}" (ID: {anilist_id}) | ' + " | ".join(changes))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════


def osd_message(msg: str) -> None:
    """Display an on-screen display (OSD) message."""
    print(f"OSD:{msg}")
    print(f"{msg}")


def open_anilist(anime_name: str, anime_id: int) -> None:
    """Open the given anime ID on AniList in a web browser."""
    url = f"https://anilist.co/anime/{anime_id}"
    osd_message(f'Opening AniList for "{anime_name}": {url}')
    webbrowser.open_new_tab(url)


def main() -> None:
    """Main entry point for the script."""
    try:
        # Reconfigure to utf-8
        if sys.stdout.encoding != "utf-8":
            try:
                sys.stdout.reconfigure(encoding="utf-8")  # type: ignore
                sys.stderr.reconfigure(encoding="utf-8")  # type: ignore
            except Exception as e_reconfigure:
                print(f"Couldn't reconfigure stdout/stderr to UTF-8: {e_reconfigure}", file=sys.stderr)

        # Parse options from argv[3] if present
        options = {
            "SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE": False,
            "UPDATE_PROGRESS_WHEN_REWATCHING": True,
            "SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT": False,
            "SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING": True,
            "ADD_ENTRY_IF_MISSING": False,
        }
        if len(sys.argv) > 3:
            user_options = json.loads(sys.argv[3])
            options.update(user_options)

        # Pass options to AniListUpdater
        updater = AniListUpdater(options, sys.argv[2])

        if sys.argv[2] == "correct" and len(sys.argv) > 6:
            updater.correct_anime_id(sys.argv[1], int(sys.argv[4]), int(sys.argv[5]), sys.argv[6])
        else:
            updater.handle_filename(sys.argv[1])

    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
