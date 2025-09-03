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
from typing import Any, Optional

import requests
from guessit import guessit  # type: ignore

# ═══════════════════════════════════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════


@dataclass
class SeasonEpisodeInfo:
    """Season and episode info for absolute numbering."""

    season_id: Optional[int]
    season_title: Optional[str]
    progress: Optional[int]
    episodes: Optional[int]
    relative_episode: Optional[int]


@dataclass
class AnimeInfo:
    """Anime information including progress and status."""

    anime_id: Optional[int]
    anime_name: Optional[str]
    current_progress: Optional[int]
    total_episodes: Optional[int]
    file_progress: Optional[int]
    current_status: Optional[str]

    # Can not specify the type further. Causes some of the the variables type checking to be unhappy.
    def __iter__(self) -> Iterator[Any]:  # fmt: off
        """Allow tuple unpacking of AnimeInfo."""
        return iter((self.anime_id, self.anime_name, self.current_progress, self.total_episodes, self.file_progress, self.current_status))  # fmt: off


@dataclass
class FileInfo:
    """Parsed filename information."""

    name: str
    episode: int
    year: str


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════
# GRAPHQL QUERIES
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════


class AniListQueries:
    """GraphQL queries for AniList API operations."""

    # Query to search for anime with optional filters
    # Variables: search (String), year (FuzzyDateInt), page (Int), onList (Boolean)
    SEARCH_ANIME = """
        query($search: String, $year: FuzzyDateInt, $page: Int, $onList: Boolean) {
            Page(page: $page) {
                media (search: $search, type: ANIME, startDate_greater: $year, onList: $onList) {
                    id
                    title { romaji }
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


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════
# MAIN ANILIST UPDATER CLASS
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════


class AniListUpdater:
    """AniList authentication, file parsing, API requests, and progress updates."""

    ANILIST_API_URL: str = "https://graphql.anilist.co"
    TOKEN_PATH: str = os.path.join(os.path.dirname(__file__), "anilistToken.txt")
    CACHE_PATH: str = os.path.join(os.path.dirname(__file__), "cache.json")
    OPTIONS: str = "--excludes country --excludes language --type episode"
    CACHE_REFRESH_RATE: int = 24 * 60 * 60

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
        self.access_token: Optional[str] = self.load_access_token()
        self.options: dict[str, Any] = options
        self.ACTION: str = action
        self._cache: Optional[dict[str, Any]] = None

    # Load token from anilistToken.txt
    def load_access_token(self) -> Optional[str]:
        """
        Load access token from file, supporting legacy formats.

        Returns:
            Optional[str]: Access token or None if not found.
        """
        try:
            if not os.path.exists(self.TOKEN_PATH):
                return None
            with open(self.TOKEN_PATH, encoding="utf-8") as f:
                lines = f.read().splitlines()
            if not lines:
                return None

            # Check for legacy formats and clean them up if found
            has_legacy_cache = any(";;" in ln for ln in lines)
            has_legacy_user_id = ":" in lines[0] and lines[0].split(":", 1)[0].isdigit()

            # Cleans up the file and returns the token directly
            if has_legacy_cache or has_legacy_user_id:
                return self.cleanup_legacy_formats(lines, has_legacy_user_id)

            # If no legacy formats, the first line should have the token.
            return lines[0].strip()

        except Exception as e:
            print(f"Error reading access token: {e}")
            return None

    def cleanup_legacy_formats(self, lines: list[str], has_legacy_user_id: bool) -> str:
        """
        Clean legacy cache entries and user_id from token file.

        Args:
            lines (list[str]): Lines read from token file.
            has_legacy_user_id (bool): Whether first line has user_id:token format.

        Returns:
            str: Cleaned token.
        """
        token = ""
        try:
            header = lines[0] if lines else ""

            # Extract just the token if it's in user_id:token format
            token = header.split(":", 1)[1].strip() if has_legacy_user_id and ":" in header else header.strip()

            # Rewrite token file with just the token, removing user_id and cache lines
            with open(self.TOKEN_PATH, "w", encoding="utf-8") as f:
                f.write(token + ("\n" if token else ""))

            if has_legacy_user_id:
                print("Cleaned up legacy user_id from token file.")
            if any(";;" in ln for ln in lines):
                print("Cleaned up legacy cache entries from token file.")
        except Exception as e:
            print(f"Legacy format cleanup failed: {e}")

        return token

    # ──────────────────────────────────────────────────────────────────────────────────────────────────
    # CACHE MANAGEMENT
    # ──────────────────────────────────────────────────────────────────────────────────────────────────

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
            dir_hash = self.hash_path(os.path.dirname(path))
            cache = self.load_cache()

            anime_id, _, current_progress, total_episodes, relative_progress, current_status = result

            now = time.time()

            cache[dir_hash] = {
                "guessed_name": guessed_name,
                "anime_id": anime_id,
                "current_progress": current_progress,
                "relative_progress": f"{absolute_progress}->{relative_progress}",
                "total_episodes": total_episodes,
                "current_status": current_status,
                "ttl": now + self.CACHE_REFRESH_RATE,
            }

            self.save_cache(cache)
        except Exception as e:
            print(f"Error trying to cache {result}: {e}")

    def hash_path(self, path: str) -> str:
        """
        Generate SHA256 hash of path.

        Args:
            path (str): Path to hash.

        Returns:
            str: Hashed path.
        """
        return hashlib.sha256(path.encode("utf-8")).hexdigest()

    def check_and_clean_cache(self, path: str, guessed_name: str) -> Optional[dict[str, Any]]:
        """
        Get valid cache entry and clean expired entries.

        Args:
            path (str): Path to media file.
            guessed_name (str): Guessed anime name.

        Returns:
            Optional[dict[str, Any]]: Cache entry or None if not found/valid.
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
            if changed:
                self.save_cache(cache)

            dir_hash = self.hash_path(os.path.dirname(path))
            entry = cache.get(dir_hash)

            if entry and entry.get("guessed_name") == guessed_name:
                return entry

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
    def make_api_request(
        self, query: str, variables: Optional[dict[str, Any]] = None, access_token: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """
        Make POST request to AniList GraphQL API.

        Args:
            query (str): GraphQL query string.
            variables (Optional[dict[str, Any]]): Query variables.
            access_token (Optional[str]): AniList access token.

        Returns:
            Optional[dict[str, Any]]: API response or None on error.
        """
        headers = {"Content-Type": "application/json", "Accept": "application/json"}

        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"

        response = requests.post(
            self.ANILIST_API_URL, json={"query": query, "variables": variables}, headers=headers, timeout=10
        )
        if response.status_code == 200:
            return response.json()
        print(
            f"API request failed: {response.status_code} - {response.text}\nQuery: {query}\nVariables: {variables}"
        )
        return None

    # ──────────────────────────────────────────────────────────────────────────────────────────────────
    # SEASON & EPISODE HANDLING
    # ──────────────────────────────────────────────────────────────────────────────────────────────────

    @staticmethod
    def season_order(season: Optional[str]) -> int:
        """
        Get numeric order for season sorting.

        Args:
            season (Optional[str]): Season name (WINTER, SPRING, SUMMER, FALL).

        Returns:
            int: Order value.
        """
        return {"WINTER": 1, "SPRING": 2, "SUMMER": 3, "FALL": 4}.get(season, 5)  # type: ignore

    # Finds the season and episode of an anime with absolute numbering
    def find_season_and_episode(self, seasons: list[dict[str, Any]], absolute_episode: int) -> SeasonEpisodeInfo:
        """
        Find correct season and relative episode for absolute episode number.

        Args:
            seasons (list[dict[str, Any]]): Season dicts.
            absolute_episode (int): Absolute episode number.

        Returns:
            SeasonEpisodeInfo: Season and episode information.
        """
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

        # If launching and cache has anime_id, we can skip search and open directly.
        if self.ACTION == "launch" and cache_entry and cache_entry.get("anime_id"):
            anime_id = cache_entry["anime_id"]
            print(f'Opening AniList (cached) for guessed "{file_info.name}": https://anilist.co/anime/{anime_id}')
            osd_message(f'Opening AniList for "{file_info.name}"')
            webbrowser.open_new_tab(f"https://anilist.co/anime/{anime_id}")
            return

        # Use cached data if available, otherwise fetch fresh info
        if cache_entry:
            print(f'Using cached data for "{file_info.name}"')

            left, right = cache_entry.get("relative_progress", "0->0").split("->")
            # For example, if 19->7, that means that 19 absolute is equivalent to 7 relative to this season
            # File episode 20: 18 - 19 + 7 = 8 relative to this season
            offset = int(left) - int(right)

            relative_episode = file_info.episode - offset

            if 1 <= relative_episode <= (cache_entry.get("total_episodes") or 0):
                # Reconstruct result from cache
                result = AnimeInfo(
                    cache_entry["anime_id"],
                    cache_entry["guessed_name"],
                    cache_entry["current_progress"],
                    cache_entry["total_episodes"],
                    relative_episode,
                    cache_entry["current_status"],
                )
            else:
                result = self.get_anime_info_and_progress(file_info.name, file_info.episode, file_info.year)

        else:
            result = self.get_anime_info_and_progress(file_info.name, file_info.episode, file_info.year)

        result = self.update_episode_count(result)

        if result and result.current_progress is not None:
            # Update cache with latest data
            self.cache_to_file(filename, file_info.name, file_info.episode, result)
        return

    # Hardcoded exceptions to fix detection
    # Easier than just renaming my files 1 by 1 on Qbit
    # Every exception I find will be added here
    def fix_filename(self, path_parts: list[str]) -> list[str]:
        """
        Apply hardcoded fixes to filename/folder structure for better detection.

        Args:
            path_parts (list[str]): Path components.

        Returns:
            list[str]: Modified path components.
        """
        # Simply easier for fixing the filename if we have what it is detecting.
        guess = guessit(path_parts[-1], self.OPTIONS)

        path_parts[-1] = os.path.splitext(path_parts[-1])[0]
        pattern = r'[\\\/:!\*\?"<>\|\._-]'

        title_depth = -1

        # Fix from folders if the everything is not in the filename
        if "title" not in guess:
            for depth in range(2, min(4, len(path_parts))):
                folder_guess = guessit(path_parts[-depth], self.OPTIONS)
                if "title" in folder_guess:
                    guess["title"] = folder_guess["title"]
                    title_depth = -depth
                    break

        if "title" not in guess:
            print(f"Couldn't find title in filename '{path_parts[-1]}'! Guess result: {guess}")
            return path_parts

        # Only clean up titles for some series
        cleanup_titles = ["Ranma", "Chi", "Bleach", "Link Click"]
        if any(title in guess["title"] for title in cleanup_titles):
            path_parts[title_depth] = re.sub(pattern, " ", path_parts[title_depth])
            path_parts[title_depth] = " ".join(path_parts[title_depth].split())

        if guess["title"] == "Centimeters per Second" and guess.get("episode", 0) == 5:
            path_parts[title_depth] = path_parts[title_depth].replace(" 5 ", " Five ")
            # For some reason AniList has this film in 3 parts.
            path_parts[title_depth] = path_parts[title_depth].replace("per Second", "per Second 3")

        # Remove 'v2', 'v3'... from the title since it fucks up with episode detection
        match = re.search(r"(E\d+)v\d", path_parts[title_depth])
        if match:
            episode = match.group(1)
            path_parts[title_depth] = path_parts[title_depth].replace(match.group(0), episode)

        return path_parts

    # Parse the file name using guessit
    def parse_filename(self, filepath: str) -> FileInfo:
        """
        Parse filename/folder structure to extract anime info.

        Args:
            filepath (str): Path to video file.

        Returns:
            FileInfo: Parsed info with name, episode, year.
        """
        path_parts = self.fix_filename(filepath.replace("\\", "/").split("/"))
        filename = path_parts[-1]
        name, season, part, year = "", "", "", ""
        remaining: list[int] = []
        episode = 1
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

        # If the title is not in the filename or episode index is 0, try the folder name
        # If the episode index > 0 and season index > 0, its safe to assume that the title is in the file name

        if title_in_filename:
            name = guess["title"]
        else:
            # If it isnt in the name of the file, try to guess using the name of the folder it is stored in

            # Depth=2 folders
            for depth in [2, 3]:
                folder_guess = guessit(path_parts[-depth], self.OPTIONS) if len(path_parts) > depth - 1 else None
                if folder_guess:
                    print(
                        f"{depth - 1}{'st' if depth - 1 == 1 else 'nd'} Folder guess:\n{path_parts[-depth]} -> {dict(folder_guess)}"
                    )

                    name = str(folder_guess.get("title", ""))
                    season = season or str(folder_guess.get("season", ""))
                    part = part or str(folder_guess.get("part", ""))
                    year = year or str(folder_guess.get("year", ""))

                    # If we got the name, its probable we already got season and part from the way folders are usually structured
                    if not name:
                        break

        # Haven't tested enough but seems to work fine
        if remaining:
            # If there are remaining episodes, append them to the name
            name += " " + " ".join(str(ep) for ep in remaining)

        # Add season and part if there are
        if season and (int(season) > 1 or part):
            name += f" Season {season}"

        if part:
            name += f" Part {part}"

        print("Guessed name: " + name)
        return FileInfo(name, episode, year)

    # ──────────────────────────────────────────────────────────────────────────────────────────────────
    # ANIME INFO & PROGRESS UPDATES
    # ──────────────────────────────────────────────────────────────────────────────────────────────────

    def filter_valid_seasons(self, seasons: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Filter and sort valid TV seasons for absolute numbering.

        Args:
            seasons (list[dict[str, Any]]): Season dicts from AniList API.

        Returns:
            list[dict[str, Any]]: Filtered and sorted seasons.
        """
        # Filter only to those whose format is TV and duration > 21 OR those who have no duration and are releasing.
        # This is due to newly added anime having duration as null
        seasons = [
            season
            for season in seasons
            if (
                (season["duration"] is None and season["status"] == "RELEASING")
                or (season["duration"] is not None and season["duration"] > 21)
            )
            and season["format"] == "TV"
        ]
        # One of the problems with this filter is needing the format to be 'TV'
        # But if accepted any format, it would also include many ONA's which arent included in absolute numbering.

        # Sort them based on release date
        seasons = sorted(
            seasons,
            key=lambda x: (
                x["seasonYear"] or float("inf"),
                self.season_order(x["season"]),
            ),
        )
        return seasons

    def get_anime_info_and_progress(self, name: str, file_progress: int, year: str) -> AnimeInfo:
        """
        Query AniList for anime info and user progress.

        Args:
            name (str): Anime title.
            file_progress (int): Episode number from file.
            year (str): Year string (may be empty).

        Returns:
            AnimeInfo: Complete anime information.

        Raises:
            Exception: If the update fails.
        """
        # Only those that are in the user's list
        query = AniListQueries.SEARCH_ANIME
        variables = {"search": name, "year": year or 1, "page": 1, "onList": True}

        response = self.make_api_request(query, variables, self.access_token)

        if not response or "data" not in response:
            return AnimeInfo(None, None, None, None, None, None)

        seasons = response["data"]["Page"]["media"]

        # No results from the API request
        if not seasons:
            # For launch action or ADD_ENTRY_IF_MISSING, search all anime (not just user's list)
            if self.ACTION == "launch" or self.options.get("ADD_ENTRY_IF_MISSING", False):
                print(f"Anime '{name}' not found in your list. Searching all anime...")
                variables["onList"] = False
                response = self.make_api_request(query, variables, self.access_token)

                if not response or "data" not in response:
                    return AnimeInfo(None, None, None, None, None, None)

                seasons = response["data"]["Page"]["media"]
                if not seasons:
                    raise Exception(f"Couldn't find an anime from this title! ({name})")

                # If this is an ADD_ENTRY_IF_MISSING request, prepare anime data for potential addition
                if self.ACTION != "launch" and self.options.get("ADD_ENTRY_IF_MISSING", False):
                    anime_to_add = seasons[0]
                    anime_id = anime_to_add["id"]
                    anime_title = anime_to_add["title"]["romaji"]

                    # Return AnimeInfo with None progress to indicate it needs to be added to list
                    # The addition will happen in update_episode_count when update is actually triggered
                    return AnimeInfo(anime_id, anime_title, None, anime_to_add["episodes"], file_progress, None)
            else:
                raise Exception(f"Couldn't find an anime from this title! ({name}). Is it in your list?")

        # This is the first element, which is the same as Media(search: $search)
        entry = seasons[0]["mediaListEntry"]
        anime_data = AnimeInfo(
            seasons[0]["id"],
            seasons[0]["title"]["romaji"],
            entry["progress"] if entry is not None else None,
            seasons[0]["episodes"],
            file_progress,
            entry["status"] if entry is not None else None,
        )

        # If the episode in the file name is larger than the total amount of episodes
        # Then they are using absolute numbering format for episodes
        # Try to guess season and episode.
        if seasons[0]["episodes"] is not None and file_progress > seasons[0]["episodes"]:
            seasons = self.filter_valid_seasons(seasons)
            print("Related shows:", ", ".join(season["title"]["romaji"] for season in seasons))
            season_episode_info = self.find_season_and_episode(seasons, file_progress)
            print(season_episode_info)
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
            )
            print(f"Final guessed anime: {found_season}")
            print(
                f"Absolute episode {file_progress} corresponds to Anime: {anime_data.anime_name}, Episode: {anime_data.file_progress}"
            )
        else:
            print(f"Final guessed anime: {seasons[0]}")
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

        anime_id, anime_name, current_progress, total_episodes, file_progress, current_status = result

        if anime_id is None:
            raise Exception("Couldn't find that anime! Make sure it is on your list and the title is correct.")

        # Only launch anilist
        if self.ACTION == "launch":
            osd_message(f'Opening AniList for "{anime_name}"')
            print(f'Opening AniList for "{anime_name}": https://anilist.co/anime/{anime_id}')
            webbrowser.open_new_tab(f"https://anilist.co/anime/{anime_id}")
            return result

        # Handle adding anime to list if it's not already there (ADD_ENTRY_IF_MISSING feature)
        if current_progress is None and current_status is None:
            # This indicates anime was found in search but is not in user's list
            if self.options.get("ADD_ENTRY_IF_MISSING", False):
                print(f'Adding "{anime_name}" to your list since you\'re watching it...')

                # Since user is actively watching this anime, always set to CURRENT
                initial_status = "CURRENT"

                # Add to list
                if self.add_anime_to_list(anime_id, anime_name, initial_status, file_progress):
                    osd_message(f'Added "{anime_name}" to your list with progress: {file_progress}')
                    print(f'Successfully added "{anime_name}" to your list with progress: {file_progress}')
                    # Return updated result
                    return AnimeInfo(
                        anime_id, anime_name, file_progress, total_episodes, file_progress, initial_status
                    )
                raise Exception(f"Failed to add '{anime_name}' to your list.")
            raise Exception("Failed to get current episode count. Is it on your list?")

        # Handle completed -> rewatching on first episode
        if (
            current_status == "COMPLETED"
            and file_progress == 1
            and self.options["SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE"]
        ):
            # Needs to update in 2 steps, since AniList
            # doesn't allow setting progress while changing the status from completed to rewatching.
            # If you try, it will just reset the progress to 0.
            print(
                "Setting status to REPEATING (rewatching) and updating progress for first episode of completed anime."
            )

            # Step 1: Set to REPEATING, progress=0
            query = AniListQueries.SAVE_MEDIA_LIST_ENTRY

            variables = {"mediaId": anime_id, "progress": 0, "status": "REPEATING"}
            response = self.make_api_request(query, variables, self.access_token)

            # Step 2: Set progress to 1
            variables = {"mediaId": anime_id, "progress": 1}
            response = self.make_api_request(query, variables, self.access_token)

            if response and "data" in response:
                updated_progress = response["data"]["SaveMediaListEntry"]["progress"]
                osd_message(f'Updated "{anime_name}" to REPEATING with progress: {updated_progress}')
                print(f"Episode count updated successfully! New progress: {updated_progress}")

                return AnimeInfo(anime_id, anime_name, updated_progress, total_episodes, 1, "REPEATING")
            print("Failed to update episode count.")
            raise Exception("Failed to update episode count.")

        # Handle updating progress for rewatching
        if current_status == "REPEATING" and self.options["UPDATE_PROGRESS_WHEN_REWATCHING"]:
            print("Updating progress for anime set to REPEATING (rewatching).")
            status_to_set = "REPEATING"

        # Only update if status is CURRENT or PLANNING
        elif current_status in {"CURRENT", "PLANNING"}:
            # If its lower than the current progress, dont update.
            if file_progress and current_progress is not None and file_progress <= current_progress:
                raise Exception(f"Episode was not new. Not updating ({file_progress} <= {current_progress})")

            status_to_set = "CURRENT"

        else:
            raise Exception(f"Anime is not in a modifiable state (status: {current_status}). Not updating.")

        # Set to COMPLETED if last episode and the option is enabled
        if file_progress == total_episodes and (
            (current_status == "CURRENT" and self.options["SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT"])
            or (current_status == "REPEATING" and self.options["SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING"])
        ):
            status_to_set = "COMPLETED"

        query = AniListQueries.SAVE_MEDIA_LIST_ENTRY

        variables = {"mediaId": anime_id, "progress": file_progress}
        if status_to_set:
            variables["status"] = status_to_set

        response = self.make_api_request(query, variables, self.access_token)
        if response and "data" in response:
            updated_progress = response["data"]["SaveMediaListEntry"]["progress"]
            osd_message(f'Updated "{anime_name}" to: {updated_progress}')
            print(f"Episode count updated successfully! New progress: {updated_progress}")
            updated_status = response["data"]["SaveMediaListEntry"]["status"]

            return AnimeInfo(anime_id, anime_name, updated_progress, total_episodes, file_progress, updated_status)
        print("Failed to update episode count.")
        raise Exception("Failed to update episode count.")

    def add_anime_to_list(
        self, anime_id: int, anime_name: str, initial_status: str = "PLANNING", initial_progress: int = 0
    ) -> bool:
        """
        Add an anime to the user's AniList.

        Args:
            anime_id (int): AniList anime ID.
            anime_name (str): Anime title for logging.
            initial_status (str): Initial status to set (default: 'PLANNING').
            initial_progress (int): Initial progress to set (default: 0).

        Returns:
            bool: True if successfully added, False otherwise.
        """
        try:
            query = AniListQueries.SAVE_MEDIA_LIST_ENTRY
            variables = {"mediaId": anime_id, "status": initial_status, "progress": initial_progress}

            response = self.make_api_request(query, variables, self.access_token)

            if response and "data" in response and response["data"]["SaveMediaListEntry"]:
                return True
            print(f'Failed to add "{anime_name}" to your list.')
            return False
        except Exception as e:
            print(f'Error adding "{anime_name}" to list: {e}')
            return False


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════


def osd_message(msg: str) -> None:
    """Display an on-screen display (OSD) message."""
    print(f"OSD:{msg}")


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
        updater.handle_filename(sys.argv[1])

    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
