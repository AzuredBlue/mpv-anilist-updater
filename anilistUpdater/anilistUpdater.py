"""
mpv-anilist-updater: Automatically updates your AniList based on the file you just watched in MPV.

This script parses anime filenames, determines the correct AniList entry, and updates your progress
or status accordingly.
"""

# Configuration options for anilistUpdater (set in anilistUpdater.conf):
#
# DIRECTORIES: List or comma/semicolon-separated string. The directories the script will work on. Leaving it empty will make it work on every video you watch with mpv. Example: DIRECTORIES = ["D:/Torrents", "D:/Anime"]
#
# UPDATE_PERCENTAGE: Integer (0-100). The percentage of the video you need to watch before it updates AniList automatically. Default is 85 (usually before the ED of a usual episode duration).
#
# SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE: Boolean. If true, when watching episode 1 of a completed anime, set it to rewatching and update progress.
#
# UPDATE_PROGRESS_WHEN_REWATCHING: Boolean. If true, allow updating progress for anime set to rewatching. This is for if you want to set anime to rewatching manually, but still update progress automatically.
#
# SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT: Boolean. If true, set to COMPLETED after last episode if status was CURRENT.
#
# SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING: Boolean. If true, set to COMPLETED after last episode if status was REPEATING (rewatching).

import sys
import os
import webbrowser
import time
import hashlib
import re
import json
import requests
from guessit import guessit

class AniListUpdater:
    """
    Handles AniList authentication, file parsing, API requests, and updating anime progress/status.
    """
    ANILIST_API_URL = 'https://graphql.anilist.co'
    TOKEN_PATH = os.path.join(os.path.dirname(__file__), 'anilistToken.txt')
    CACHE_PATH = os.path.join(os.path.dirname(__file__), 'cache.json')
    OPTIONS = "--excludes country --excludes language --type episode"
    CACHE_REFRESH_RATE =  24 * 60 * 60

    # Load token
    def __init__(self, options, action):
        """
        Initializes the AniListUpdater, loading the access token.
        """
        self.access_token = self.load_access_token()
        self.options = options
        self.ACTION = action
        self._cache = None

    # Load token from anilistToken.txt
    def load_access_token(self):
        """
        Loads access token in a single file read.
        Token file formats supported:
          - token_only
          - user_id:token (legacy - user_id will be removed)
          (legacy cache lines with ';;' are also cleaned up if found)
        Returns:
            str or None: access_token or None
        """
        try:
            if not os.path.exists(self.TOKEN_PATH):
                return None
            with open(self.TOKEN_PATH, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()
            if not lines:
                return None

            # Check for legacy formats and clean them up if found
            has_legacy_cache = any(';;' in ln for ln in lines)
            has_legacy_user_id = ':' in lines[0] and lines[0].split(':', 1)[0].isdigit()

            if has_legacy_cache or has_legacy_user_id:
                self._cleanup_legacy_formats(lines, has_legacy_user_id)

            header = lines[0].strip()
            token = None
            if ':' in header:
                left, right = header.split(':', 1)
                if left.isdigit():
                    # Legacy user_id:token format
                    token = right.strip()
                else:
                    token = header.strip()
            else:
                token = header.strip()
            if token == '':
                token = None
            return token
        except Exception as e:
            print(f'Error reading access token: {e}')
            return None

    def _cleanup_legacy_formats(self, lines, has_legacy_user_id):
        """
        Removes legacy cache entries and user_id from token file using already-read lines.
        Args:
            lines (list): The lines already read from the token file.
            has_legacy_user_id (bool): Whether the first line has user_id:token format.
        """
        try:
            header = lines[0] if lines else ''

            # Extract just the token if it's in user_id:token format
            if has_legacy_user_id and ':' in header:
                token = header.split(':', 1)[1].strip()
            else:
                token = header.strip()

            # Rewrite token file with just the token, removing user_id and cache lines
            with open(self.TOKEN_PATH, 'w', encoding='utf-8') as f:
                f.write(token + ('\n' if token else ''))

            if has_legacy_user_id:
                print('Cleaned up legacy user_id from token file.')
            if any(';;' in ln for ln in lines):
                print('Cleaned up legacy cache entries from token file.')
        except Exception as e:
            print(f'Legacy format cleanup failed: {e}')


    def cache_to_file(self, path, guessed_name, result):
        """
        Stores/updates a structured cache entry in cache.json.
        Cache schema: hash -> { guessed_name, anime_id, current_progress, total_episodes, current_status, ttl }
        ttl is an absolute epoch time (expiry moment).
        Args:
            path (str): The file path.
            guessed_name (str): The guessed anime name.
            result (tuple): The result to cache (anime_id, anime_name, current_progress, total_episodes, file_progress, current_status).
        """
        try:
            dir_hash = self.hash_path(os.path.dirname(path))
            cache = self.load_cache()
            if result is not None:
                anime_id = result[0]
                current_progress = result[2]
                total_episodes = result[3]
                current_status = result[5]
                now = time.time()
                cache[dir_hash] = {
                    'guessed_name': guessed_name,
                    'anime_id': anime_id,
                    'current_progress': current_progress,
                    'total_episodes': total_episodes,
                    'current_status': current_status,
                    'ttl': now + self.CACHE_REFRESH_RATE
                }
                self.save_cache(cache)
        except Exception as e:
            print(f'Error trying to cache {result}: {e}')

    def hash_path(self, path):
        """
        Returns a SHA256 hash of the given path.
        Args:
            path (str): The path to hash.
        Returns:
            str: The hashed path.
        """
        return hashlib.sha256(path.encode('utf-8')).hexdigest()

    def check_and_clean_cache(self, path, guessed_name):
        """
        Returns structured cache entry if valid. Cleans expired entries.
        Returns (entry_dict or None).
        Args:
            path (str): The path to the media file.
            guessed_name (str): The guessed name of the anime.
        """
        try:
            cache = self.load_cache()
            now = time.time()
            changed = False
            # Purge expired
            for k, v in list(cache.items()):
                if v.get('ttl', 0) < now:
                    cache.pop(k, None)
                    changed = True
            if changed:
                self.save_cache(cache)

            dir_hash = self.hash_path(os.path.dirname(path))
            entry = cache.get(dir_hash)
            if entry and entry.get('guessed_name') == guessed_name and entry.get('ttl', 0) >= now:
                return entry
            return None
        except Exception as e:
            print(f'Error trying to read cache file: {e}')
            return None


    def load_cache(self):
        """
        Loads the cache from the CACHE_PATH JSON file with lazy loading.
        Returns the cached data if already loaded, otherwise loads from file.
        Returns an empty dictionary if the file does not exist or an error occurs.
        """
        if self._cache is None:
            try:
                if not os.path.exists(self.CACHE_PATH):
                    self._cache = {}
                else:
                    with open(self.CACHE_PATH, 'r', encoding='utf-8') as f:
                        self._cache = json.load(f)
            except Exception:
                self._cache = {}
        return self._cache

    def save_cache(self, cache):
        """
        Saves the cache dictionary to the CACHE_PATH JSON file and updates the local cache.
        Args:
            cache (dict): The cache data to save.
        """
        try:
            with open(self.CACHE_PATH, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            # Keep local cache in sync
            self._cache = cache
        except Exception as e:
            print(f'Failed saving cache.json: {e}')

    # Function to make an api request to AniList's api
    def make_api_request(self, query, variables=None, access_token=None):
        """
        Makes a POST request to the AniList GraphQL API.
        Args:
            query (str): The GraphQL query string.
            variables (dict, optional): Variables for the query.
            access_token (str, optional): AniList access token.
        Returns:
            dict or None: The API response as a dict, or None on error.
        """
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        if access_token:
            headers['Authorization'] = f'Bearer {access_token}'

        response = requests.post(self.ANILIST_API_URL, json={'query': query, 'variables': variables}, headers=headers, timeout=10)
        # print(f"Made an API Query with: Query: {query}\nVariables: {variables} ")
        if response.status_code == 200:
            return response.json()
        print(f'API request failed: {response.status_code} - {response.text}\nQuery: {query}\nVariables: {variables}')
        return None

    @staticmethod
    def season_order(season):
        """
        Returns a numeric order for seasons for sorting.
        Args:
            season (str): The season name (WINTER, SPRING, SUMMER, FALL).
        Returns:
            int: The order value.
        """
        return {'WINTER': 1, 'SPRING': 2, 'SUMMER': 3, 'FALL': 4}.get(season, 5)

    def filter_valid_seasons(self, seasons):
        """
        Filters and sorts valid TV seasons for absolute numbering logic.
        Args:
            seasons (list): List of season dicts from AniList API.
        Returns:
            list: Filtered and sorted list of seasons.
        """
        # Filter only to those whose format is TV and duration > 21 OR those who have no duration and are releasing.
        # This is due to newly added anime having duration as null
        seasons = [
                    season for season in seasons
                if ((season['duration'] is None and season['status'] == 'RELEASING') or
                   (season['duration'] is not None and season['duration'] > 21)) and season['format'] == 'TV'
                ]
                # One of the problems with this filter is needing the format to be 'TV'
                # But if accepted any format, it would also include many ONA's which arent included in absolute numbering.

                # Sort them based on release date
        seasons = sorted(seasons, key=lambda x: (x['seasonYear'] if x['seasonYear'] else float("inf"), self.season_order(x['season'] if x['season'] else float("inf"))))
        return seasons

    # Finds the season and episode of an anime with absolute numbering
    def find_season_and_episode(self, seasons, absolute_episode):
        """
        Finds the correct season and relative episode for an absolute episode number.
        Args:
            seasons (list): List of season dicts.
            absolute_episode (int): The absolute episode number.
        Returns:
            tuple: (season_id, season_title, progress, episodes, relative_episode)
        """
        accumulated_episodes = 0
        for season in seasons:
            season_episodes = season.get('episodes', 12) if season.get('episodes') else 12

            if accumulated_episodes + season_episodes >= absolute_episode:
                return (
                    season.get('id'),
                    season.get('title', {}).get('romaji'),
                    season.get('mediaListEntry', {}).get('progress') if season.get('mediaListEntry') else None,
                    season.get('episodes'),
                    absolute_episode - accumulated_episodes
                )
            accumulated_episodes += season_episodes
        return (None, None, None, None, None)

    def handle_filename(self, filename):
        """
        Main entry point for handling a file: parses, checks cache, updates AniList, and manages cache.
        Args:
            filename (str): The path to the video file.
        """
        file_info = self.parse_filename(filename)
        cache_entry = self.check_and_clean_cache(filename, file_info.get('name'))

        # If launching and cache has anime_id, we can skip search and open directly.
        if self.ACTION == 'launch' and cache_entry and cache_entry.get('anime_id'):
            anime_id = cache_entry['anime_id']
            print(f'Opening AniList (cached) for guessed "{file_info.get("name")}": https://anilist.co/anime/{anime_id}')
            webbrowser.open_new_tab(f'https://anilist.co/anime/{anime_id}')
            return

        # Use cached data if available, otherwise fetch fresh info
        if cache_entry:
            # Reconstruct result tuple from cache
            result = (
                cache_entry['anime_id'],
                cache_entry['guessed_name'],
                cache_entry['current_progress'],
                cache_entry['total_episodes'],
                file_info.get('episode'),
                cache_entry['current_status']
            )
            print(f'Using cached data for "{file_info.get("name")}"')
        else:
            result = self.get_anime_info_and_progress(file_info.get('name'), file_info.get('episode'), file_info.get('year'))

        result = self.update_episode_count(result)

        if result and result[2] is not None:
            # Update cache with latest data
            self.cache_to_file(filename, file_info.get('name'), result)
        return

    # Hardcoded exceptions to fix detection
    # Easier than just renaming my files 1 by 1 on Qbit
    # Every exception I find will be added here
    def fix_filename(self, path_parts):
        """
        Applies hardcoded exceptions and fixes to the filename and folder structure for better title detection.
        Args:
            path_parts (list): List of path components.
        Returns:
            list: Modified path components.
        """
        guess = guessit(path_parts[-1], self.OPTIONS) # Simply easier for fixing the filename if we have what it is detecting.

        path_parts[-1] = os.path.splitext(path_parts[-1])[0]
        pattern = r'[\\\/:!\*\?"<>\|\._-]'

        title_depth = -1

        # Fix from folders if the everything is not in the filename
        if 'title' not in guess:
            # Depth=2
            for depth in range(2, min(4, len(path_parts))):
                folder_guess = guessit(path_parts[-depth], self.OPTIONS)
                if 'title' in folder_guess:
                    guess['title'] = folder_guess['title']
                    title_depth = -depth
                    break

        if 'title' not in guess:
            print(f"Couldn't find title in filename '{path_parts[-1]}'! Guess result: {guess}")
            return path_parts

        # Only clean up titles for some series
        cleanup_titles = ['Ranma', 'Chi', 'Bleach', 'Link Click']
        if any(title in guess['title'] for title in cleanup_titles):
            path_parts[title_depth] = re.sub(pattern, ' ', path_parts[title_depth])
            path_parts[title_depth] = " ".join(path_parts[title_depth].split())

        if 'Centimeters per Second' == guess['title'] and 5 == guess.get('episode', 0):
            path_parts[title_depth] = path_parts[title_depth].replace(' 5 ', ' Five ')
            # For some reason AniList has this film in 3 parts.
            path_parts[title_depth] = path_parts[title_depth].replace('per Second', 'per Second 3')

        # Remove 'v2', 'v3'... from the title since it fucks up with episode detection
        match = re.search(r'(E\d+)v\d', path_parts[title_depth])
        if match:
            episode = match.group(1)
            path_parts[title_depth] = path_parts[title_depth].replace(match.group(0), episode)

        return path_parts

    # Parse the file name using guessit
    def parse_filename(self, filepath):
        """
        Parses the filename and folder structure to extract anime title, episode, season, and year.
        Args:
            filepath (str): The path to the video file.
        Returns:
            dict: Parsed info with keys 'name', 'episode', 'year'.
        """
        path_parts = self.fix_filename(filepath.replace('\\', '/').split('/'))
        filename = path_parts[-1]
        name, season, part, year, remaining = '', '', '', '',  []
        episode = 1
        # First, try to guess from the filename
        guess = guessit(filename, self.OPTIONS)
        print(f'File name guess: {filename} -> {dict(guess)}')

        # Episode guess from the title.
        # Usually, releases are formated [Release Group] Title - S01EX

        # If the episode index is 0, that would mean that the episode is before the title in the filename
        # Which is a horrible way of formatting it, so assume its wrong

        # If its 1, then the title is probably 0, so its okay. (Unless season is 0)
        # Really? What is the format "S1E1 - {title}"? That's almost psycopathic.

        # If its >2, theres probably a Release Group and Title / Season / Part, so its good

        episode = guess.get('episode', None)
        season = guess.get('season', '')
        part = str(guess.get('part', ''))
        year = str(guess.get('year', ''))

        # Quick fixes assuming season before episode
        # 'episode_title': '02' in 'S2 02'
        if guess.get('episode_title', '').isdigit() and episode is None:
            print(f'Detected episode in episode_title. Episode: {int(guess.get("episode_title"))}')
            episode = int(guess.get('episode_title'))

        # 'episode': [86, 13] (EIGHTY-SIX), [1, 2, 3] (RANMA) lol.
        if isinstance(episode, list):
            print(f'Detected multiple episodes: {episode}. Picking last one.')
            remaining = episode[:-1]
            episode = episode[-1]

        # 'season': [2, 3] in "S2 03"
        if isinstance(season, list):
            print(f'Detected multiple seasons: {season}. Picking first one as season.')
            if episode is None:
                print('Episode still not detected. Picking last position of the season list.')
                episode = season[-1]

            season = season[0]

        episode = episode or 1
        season = str(season)

        keys = list(guess.keys())
        episode_index = keys.index('episode') if 'episode' in guess else 1
        season_index = keys.index('season') if 'season' in guess else -1
        title_in_filename = 'title' in guess and (episode_index > 0 and (season_index > 0 or season_index == -1))

        # If the title is not in the filename or episode index is 0, try the folder name
        # If the episode index > 0 and season index > 0, its safe to assume that the title is in the file name

        if title_in_filename:
            name = guess['title']
        else:
            # If it isnt in the name of the file, try to guess using the name of the folder it is stored in

            # Depth=2 folders
            for depth in [2, 3]:
                folder_guess = guessit(path_parts[-depth], self.OPTIONS) if len(path_parts) > depth-1 else ''
                if folder_guess != '':
                    print(f'{depth-1}{"st" if depth-1==1 else "nd"} Folder guess:\n{path_parts[-depth]} -> {dict(folder_guess)}')

                    name = str(folder_guess.get('title', ''))
                    season = season or str(folder_guess.get('season', ''))
                    part = part or str(folder_guess.get('part', ''))
                    year = year or str(folder_guess.get('year', ''))

                    # If we got the name, its probable we already got season and part from the way folders are usually structured
                    if name != '':
                        break

        # Haven't tested enough but seems to work fine
        if remaining:
            # If there are remaining episodes, append them to the name
            name += ' ' + ' '.join(str(ep) for ep in remaining)

        # Add season and part if there are
        if season and (int(season) > 1 or part):
            name += f" Season {season}"

        if part:
            name += f" Part {part}"

        print('Guessed name: ' + name)
        return {
            'name': name,
            'episode': episode,
            'year': year,
        }

    def get_anime_info_and_progress(self, name, file_progress, year):
        """
        Queries AniList for anime info and user progress for a given title and year.
        Args:
            name (str): Anime title.
            file_progress (int): Episode number from the file.
            year (str): Year string (may be empty).
        Returns:
            tuple: (anime_id, anime_name, current_progress, total_episodes, file_progress, current_status)
        """

        # Only those that are in the user's list

        query = '''
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
            '''
        variables = {'search': name, 'year': year or 1, 'page': 1, 'onList': True}

        response = self.make_api_request(query, variables, self.access_token)

        if not response or 'data' not in response:
            return (None, None, None, None, None, None)

        seasons = response['data']['Page']['media']

        # No results from the API request
        if not seasons:
            # Before erroring, if its a "launch" request we can search even if its not in the user list
            if self.ACTION == 'launch':
                variables['onList'] = False
                response = self.make_api_request(query, variables, self.access_token)

                if not response or 'data' not in response:
                    return (None, None, None, None, None, None)

                seasons = response['data']['Page']['media']
                # If its still empty
                if not seasons:
                    raise Exception(f"Couldn\'t find an anime from this title! ({name})")
            else:
                raise Exception(f"Couldn\'t find an anime from this title! ({name}). Is it in your list?")

        # This is the first element, which is the same as Media(search: $search)
        entry = seasons[0]['mediaListEntry']
        anime_data = (
            seasons[0]['id'],
            seasons[0]['title']['romaji'],
            entry['progress'] if entry is not None else None,
            seasons[0]['episodes'],
            file_progress,
            entry['status'] if entry is not None else None
        )

        # If the episode in the file name is larger than the total amount of episodes
        # Then they are using absolute numbering format for episodes
        # Try to guess season and episode.
        if seasons[0]['episodes'] is not None and file_progress > seasons[0]['episodes']:
            seasons = self.filter_valid_seasons(seasons)
            print('Related shows:', ", ".join(season["title"]["romaji"] for season in seasons))
            anime_data = self.find_season_and_episode(seasons, file_progress)
            print(anime_data)
            found_season = next((season for season in seasons if season['id'] == anime_data[0]), None)
            found_entry = found_season['mediaListEntry'] if found_season and found_season['mediaListEntry'] else None
            anime_data = (
                anime_data[0],
                anime_data[1],
                anime_data[2],
                anime_data[3],
                anime_data[4],
                found_entry['status'] if found_entry else None
            )
            print(f"Final guessed anime: {found_season}")
            print(f'Absolute episode {file_progress} corresponds to Anime: {anime_data[1]}, Episode: {anime_data[-2]}')
        else:
            print(f"Final guessed anime: {seasons[0]}")
        return anime_data

    # Update the anime based on file progress
    def update_episode_count(self, result):
        """
        Updates the episode count and/or status for an anime entry on AniList, according to user settings.
        Args:
            result (tuple): (anime_id, anime_name, current_progress, total_episodes, file_progress, current_status)
        Returns:
            tuple or bool: Updated result tuple, or False on failure.
        """
        if result is None:
            raise Exception('Parameter in update_episode_count is null.')

        anime_id, anime_name, current_progress, total_episodes, file_progress, current_status = result

        if anime_id is None:
            raise Exception('Couldn\'t find that anime! Make sure it is on your list and the title is correct.')

        # Only launch anilist
        if self.ACTION == 'launch':
            print(f'Opening AniList for "{anime_name}": https://anilist.co/anime/{anime_id}')
            webbrowser.open_new_tab(f'https://anilist.co/anime/{anime_id}')
            return result

        if current_progress is None:
            raise Exception('Failed to get current episode count. Is it on your list?')

        # Handle completed -> rewatching on first episode
        if (current_status == 'COMPLETED' and file_progress == 1 and self.options['SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE']):

            # Needs to update in 2 steps, since AniList
            # doesn't allow setting progress while changing the status from completed to rewatching.
            # If you try, it will just reset the progress to 0.
            print('Setting status to REPEATING (rewatching) and updating progress for first episode of completed anime.')

            # Step 1: Set to REPEATING, progress=0
            query = '''
            mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
                SaveMediaListEntry (mediaId: $mediaId, progress: $progress, status: $status) {
                    status
                    id
                    progress
                }
            }
            '''

            variables = {'mediaId': anime_id, 'progress': 0, 'status': 'REPEATING'}
            response = self.make_api_request(query, variables, self.access_token)

            # Step 2: Set progress to 1
            variables = {'mediaId': anime_id, 'progress': 1}
            response = self.make_api_request(query, variables, self.access_token)

            if response and 'data' in response:
                updated_progress = response['data']['SaveMediaListEntry']['progress']
                print(f'Episode count updated successfully! New progress: {updated_progress}')

                return (anime_id, anime_name, updated_progress, total_episodes, 1, 'REPEATING')
            print('Failed to update episode count.')

            return False

        # Handle updating progress for rewatching
        if (current_status == 'REPEATING' and self.options['UPDATE_PROGRESS_WHEN_REWATCHING']):
            print('Updating progress for anime set to REPEATING (rewatching).')
            status_to_set = 'REPEATING'

        # Only update if status is CURRENT or PLANNING
        elif current_status in ['CURRENT', 'PLANNING']:

            # If its lower than the current progress, dont update.
            if file_progress <= current_progress:
                raise Exception(f'Episode was not new. Not updating ({file_progress} <= {current_progress})')

            status_to_set = 'CURRENT'

        else:
            raise Exception(f'Anime is not in a modifiable state (status: {current_status}). Not updating.')

        # Set to COMPLETED if last episode and the option is enabled
        if file_progress == total_episodes:
            if (current_status == 'CURRENT' and self.options['SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT']) or (current_status == 'REPEATING' and self.options['SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING']):
                status_to_set = "COMPLETED"

        query = '''
        mutation ($mediaId: Int, $progress: Int, $status: MediaListStatus) {
            SaveMediaListEntry (mediaId: $mediaId, progress: $progress, status: $status) {
                status
                id
                progress
            }
        }
        '''

        variables = {'mediaId': anime_id, 'progress': file_progress}
        if status_to_set:
            variables['status'] = status_to_set

        response = self.make_api_request(query, variables, self.access_token)
        if response and 'data' in response:
            updated_progress = response['data']['SaveMediaListEntry']['progress']
            print(f'Episode count updated successfully! New progress: {updated_progress}')
            current_status = response['data']['SaveMediaListEntry']['status']

            return (anime_id, anime_name, updated_progress, total_episodes, file_progress, current_status)
        print('Failed to update episode count.')
        return False

def main():
    """
    Main entry point for the script. Handles encoding and runs the updater.
    """
    try:
        # Reconfigure to utf-8
        if sys.stdout.encoding != 'utf-8':
            try:
                sys.stdout.reconfigure(encoding='utf-8')
                sys.stderr.reconfigure(encoding='utf-8')
            except Exception as e_reconfigure:
                print(f"Couldn\'t reconfigure stdout/stderr to UTF-8: {e_reconfigure}", file=sys.stderr)

        # Parse options from argv[3] if present
        options = {
            "SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE": False,
            "UPDATE_PROGRESS_WHEN_REWATCHING": True,
            "SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT": False,
            "SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING": True
        }
        if len(sys.argv) > 3:
            user_options = json.loads(sys.argv[3])
            options.update(user_options)

        # Pass options to AniListUpdater
        updater = AniListUpdater(options, sys.argv[2])
        updater.handle_filename(sys.argv[1])

    except Exception as e:
        print(f'ERROR: {e}')
        sys.exit(1)

if __name__ == '__main__':
    main()
