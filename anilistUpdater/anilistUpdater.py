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
import ast
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
    OPTIONS = "--excludes country --excludes language --type episode"
    CACHE_REFRESH_RATE = 24 * 60 * 60

    # Load token and user id
    def __init__(self, options, action):
        """
        Initializes the AniListUpdater, loading the access token and user ID.
        """
        self.access_token = self.load_access_token() # Replace token here if you don't use the .txt
        self.user_id = self.get_user_id()
        self.options = options
        self.ACTION = action

    # Load token from anilistToken.txt
    def load_access_token(self):
        """
        Loads the AniList access token from the token file.
        Returns:
            str or None: The access token, or None if not found.
        """
        try:
            with open(self.TOKEN_PATH, 'r', encoding='utf-8') as file:
                content = file.read().strip()
                if ':' in content:
                    token = content.split(':', 1)[1].splitlines()[0]
                    return token

                return content
        except Exception as e:
            print(f'Error reading access token: {e}')
            return None

    # Load user id from file, if not then make api request and save it.
    def get_user_id(self):
        """
        Loads the AniList user ID from the token file, or fetches and caches it if not present.
        Returns:
            int or None: The user ID, or None if not found.
        """
        try:
            with open(self.TOKEN_PATH, 'r', encoding='utf-8') as file:
                content = file.read().strip()
                if ':' in content:
                    return int(content.split(':')[0])
        except Exception as e:
            print(f'Error reading user ID: {e}')

        query = '''
        query {
            Viewer {
                id
            }
        }
        '''
        response = self.make_api_request(query, None, self.access_token)
        if response and 'data' in response:
            user_id = response['data']['Viewer']['id']
            self.save_user_id(user_id)
            return user_id
        return None

    # Cache user id
    def save_user_id(self, user_id):
        """
        Saves the user ID to the token file, prepending it to the existing content.
        Args:
            user_id (int): The AniList user ID.
        """
        try:
            with open(self.TOKEN_PATH, 'r+', encoding='utf-8') as file:
                content = file.read()
                file.seek(0)
                file.write(f'{user_id}:{content}')
        except Exception as e:
            print(f'Error saving user ID: {e}')

    def cache_to_file(self, path, guessed_name, result):
        """
        Appends a cache entry to the token file for a given file path and guessed anime name.
        Args:
            path (str): The file path.
            guessed_name (str): The guessed anime name.
            result (tuple): The result to cache.
        """
        try:
            with open(self.TOKEN_PATH, 'a', encoding='utf-8') as file:
                # Epoch Time, hash of the path, guessed name, result
                file.write(f'\n{time.time()};;{self.hash_path(os.path.dirname(path))};;{guessed_name};;{result}')
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
        Checks the cache for a matching entry and cleans out expired entries.
        Args:
            path (str): The file path.
            guessed_name (str): The guessed anime name.
        Returns:
            tuple: (cached_result, line_index) or (None, None) if not found.
        """
        try:
            valid_lines = []
            unique = set()
            path = self.hash_path(os.path.dirname(path))
            cached_result = (None, None)

            with open(self.TOKEN_PATH, 'r+', encoding='utf-8') as file:
                orig_lines = file.readlines()

            for line in orig_lines:
                if line.strip():
                    if ';;' in line:
                        epoch, dir_path, guess, result = line.strip().split(';;')

                        if time.time() - float(epoch) < self.CACHE_REFRESH_RATE and (dir_path, guess) not in unique:
                            unique.add((dir_path, guess))
                            valid_lines.append(line)

                            if dir_path == path and guess == guessed_name:
                                cached_result = (result, len(valid_lines) - 1)
                    else:
                        valid_lines.append(line)

            if valid_lines != orig_lines:
                with open(self.TOKEN_PATH, 'w', encoding='utf-8') as file:
                    file.writelines(valid_lines)

            return cached_result
        except Exception as e:
            print(f'Error trying to read cache file: {e}')

    def update_cache(self, path, guessed_name, result, index):
        """
        Updates a cache entry at the given index with new data.
        Args:
            path (str): The file path.
            guessed_name (str): The guessed anime name.
            result (tuple): The result to cache.
            index (int): The line index in the cache file.
        """
        try:
            with open(self.TOKEN_PATH, 'r', encoding='utf-8') as file:
                lines = file.readlines()

            if 0 <= index < len(lines):
                # Update the line at the given index with the new cache data
                updated_line = f'{time.time()};;{self.hash_path(os.path.dirname(path))};;{guessed_name};;{result}\n' if result is not None else ''
                lines[index] = updated_line

                # Write the updated lines back to the file
                with open(self.TOKEN_PATH, 'w', encoding='utf-8') as file:
                    file.writelines(lines)

            else:
                print(f"Invalid index {index} for updating cache.")
        except Exception as e:
            print(f'Error trying to update cache file: {e}')

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
        cached_result, line_index = self.check_and_clean_cache(filename, file_info.get('name'))
        # str -> tuple
        if cached_result:
            try:
                cached_result = ast.literal_eval(cached_result)
                if not isinstance(cached_result, (tuple, list)):
                    cached_result = None
            except Exception:
                cached_result = None
        else:
            cached_result = None

        # True if:
        #   Is not cached
        #   Tries to update and current episode is not the next one.
        #   It is not in your watching/planning list.
        # This means that for shows with absolute numbering, if it updates, it will always call the API
        # Since it needs to convert from absolute to relative.
        if cached_result is None or (cached_result and (file_info.get('episode') != cached_result[2] + 1) and self.ACTION != 'launch'):
            result = self.get_anime_info_and_progress(file_info.get('name'), file_info.get('episode'), file_info.get('year'))
            result = self.update_episode_count(result) # Returns either the same, or the updated result

            # If it returned a result and the progress isnt None, then put it in cache, since it wasn't.
            if result and result[2] is not None:
                if line_index is not None:
                    print(f'Updating cache to: {result}')
                    self.update_cache(filename, file_info.get('name'), result, line_index)
                else:
                    print(f'Not found in cache! Adding to file... {result}')
                    self.cache_to_file(filename, file_info.get('name'), result)

        # True for opening AniList and updating next episode.
        else:
            print(f'Found in cache! {cached_result}')
            # Only proceed if cached_result is a tuple/list and has enough elements
            if isinstance(cached_result, (tuple, list)) and len(cached_result) >= 4:
                # Change to the episode that needs to be updated
                if len(cached_result) > 5:
                    cached_result = tuple(cached_result[:4]) + (file_info.get('episode'),) + tuple(cached_result[5:])
                else:
                    cached_result = tuple(cached_result[:4]) + (file_info.get('episode'),)
                # Ensure tuple is 6 elements (pad with "CURRENT" if needed)
                if len(cached_result) == 5:
                    cached_result = cached_result + ("CURRENT",)
                result = self.update_episode_count(cached_result)

                # If it's different, update in cache as well.
                if cached_result != result and result:
                    print(f'Updating cache to: {result}')
                    self.update_cache(filename, file_info.get('name'), result, line_index)

                # If it either errored or couldn't update, retry without cache.
                if not result:
                    print('Failed to update through cache, retrying without.')
                    # Deleting from the cache
                    self.update_cache(filename, file_info.get('name'), None, line_index)
                    # Retrying
                    self.handle_filename(filename)
            else:
                print('Cached result is invalid, ignoring cache.')
                # Remove invalid cache entry
                if line_index is not None:
                    self.update_cache(filename, file_info.get('name'), None, line_index)
                # Retry without cache
                self.handle_filename(filename)

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
            raise Exception(f'Couldn\'t find that anime! Make sure it is on your list and the title is correct.')

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