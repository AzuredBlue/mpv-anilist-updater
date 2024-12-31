import sys
import os
import webbrowser
import requests
import time
import ast
import hashlib
import re
from guessit import guessit

class AniListUpdater:
    ANILIST_API_URL = 'https://graphql.anilist.co'
    TOKEN_PATH = os.path.join(os.path.dirname(__file__), 'anilistToken.txt')
    OPTIONS = "--excludes country --excludes language --type episode"
    CACHE_REFRESH_RATE = 24*60*60

    # Load token and user id
    def __init__(self):
        self.access_token = self.load_access_token() # Replace token here if you don't use the .txt
        self.user_id = self.get_user_id()

    # Load token from anilistToken.txt
    def load_access_token(self):
        try:
            with open(self.TOKEN_PATH, 'r') as file:
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
        try:
            with open(self.TOKEN_PATH, 'r') as file:
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
        try:
            with open(self.TOKEN_PATH, 'r+') as file:
                content = file.read()
                file.seek(0)
                file.write(f'{user_id}:{content}')
        except Exception as e:
            print(f'Error saving user ID: {e}')

    def cache_to_file(self, path, guessed_name, result):
        try:
            with open(self.TOKEN_PATH, 'a') as file:
                # Epoch Time, hash of the path, guessed name, result
                file.write(f'\n{time.time()};;{self.hash_path(os.path.dirname(path))};;{guessed_name};;{result}')
        except Exception as e:
            print(f'Error trying to cache {result}: {e}')
    
    def hash_path(self, path):
        return hashlib.sha256(path.encode('utf-8')).hexdigest()

    def check_and_clean_cache(self, path, guessed_name):
        try:
            valid_lines = []
            unique = set()
            path = self.hash_path(os.path.dirname(path))
            cached_result = (None, None)

            with open(self.TOKEN_PATH, 'r+') as file:
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
                with open(self.TOKEN_PATH, 'w') as file:
                    file.writelines(valid_lines)
            
            return cached_result
        except Exception as e:
            print(f'Error trying to read cache file: {e}')

    def update_cache(self, path, guessed_name, result, index):
        try:
            with open(self.TOKEN_PATH, 'r') as file:
                lines = file.readlines()

            if 0 <= index < len(lines):
                # Update the line at the given index with the new cache data    
                updated_line = f'{time.time()};;{self.hash_path(os.path.dirname(path))};;{guessed_name};;{result}\n' if result is not None else ''
                lines[index] = updated_line

                # Write the updated lines back to the file
                with open(self.TOKEN_PATH, 'w') as file:
                    file.writelines(lines)

            else:
                print(f"Invalid index {index} for updating cache.")
        except Exception as e:
            print(f'Error trying to update cache file: {e}')

    # Function to make an api request to AniList's api
    def make_api_request(self, query, variables=None, access_token=None):
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        if access_token:
            headers['Authorization'] = f'Bearer {access_token}'
        
        response = requests.post(self.ANILIST_API_URL, json={'query': query, 'variables': variables}, headers=headers)
        # print(f"Made an API Query with: Query: {query}\nVariables: {variables} ")
        if response.status_code == 200:
            return response.json()
        else:
            print(f'API request failed: {response.status_code} - {response.text}\nQuery: {query}\nVariables: {variables}')
            return None

    @staticmethod
    def season_order(season):
        return {'WINTER': 1, 'SPRING': 2, 'SUMMER': 3, 'FALL': 4}.get(season, 5)

    def filter_valid_seasons(self, seasons):
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
        accumulated_episodes = 0
        for season in seasons:
            season_episodes = season.get('episodes', 0)
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
        file_info = self.parse_filename(filename)
        cached_result, line_index = self.check_and_clean_cache(filename, file_info.get('name'))
        # str -> tuple
        cached_result = ast.literal_eval(cached_result) if cached_result else None
        
        # True if:
        #   Is not cached
        #   Tries to update and current episode is not the next one.
        #   It is not in your watching/planning list.
        # This means that for shows with absolute numbering, if it updates, it will always call the API
        # Since it needs to convert from absolute to relative.
        if cached_result is None or (cached_result and (file_info.get('episode') != cached_result[2] + 1) and sys.argv[2] != 'launch'):
            result = self.get_anime_info_and_progress(file_info.get('name'), file_info.get('episode'), file_info.get('year'))
            result = self.update_episode_count(result) # Returns either the same, or the updated result

            # If it returned a result, then put it in cache, since it wasn't.
            if result:
                
                if line_index is not None:
                    print(f'Updating cache to: {result}')
                    self.update_cache(filename, file_info.get('name'), result, line_index)
                else:
                    print(f'Not found in cache! Adding to file... {result}')
                    self.cache_to_file(filename, file_info.get('name'), result)
        
        # True for opening AniList and updating next episode.
        else:
            print(f'Found in cache! {cached_result}')
            # Change to the episode that needs to be updated
            cached_result = cached_result[:4] + (file_info.get('episode'),)
            result = self.update_episode_count(cached_result)

            # If it's different, update in cache as well.
            if cached_result != result and result:                
                print(f'Updating cache to: {result}')
                self.update_cache(filename, file_info.get('name'), result, line_index)

            # If it either errored or couldn't update, retry without cache.
            if not result:
                print(f'Failed to update through cache, retrying without.')
                # Deleting from the cache
                self.update_cache(filename, file_info.get('name'), None, line_index)
                # Retrying
                self.handle_filename(filename)
        
        return
        
    # Hardcoded exceptions to fix detection
    # Easier than just renaming my files 1 by 1 on Qbit
    # Every exception I find will be added here
    def fix_filename(self, path_parts):
        guess = guessit(path_parts[-1], self.OPTIONS) # Simply easier for fixing the filename if we have what it is detecting.

        path_parts[-1] = os.path.splitext(path_parts[-1])[0]

        pattern = r'[\\\/:!\*\?"<>\|\._-]'
        title_depth = -1

        # Replace special characters
        path_parts[-1] = re.sub(pattern, ' ', path_parts[-1])

        # Remove multiple spaces
        path_parts[-1] = " ".join(path_parts[-1].split())

        # Fix from folders if the everything is not in the filename
        if 'title' not in guess:
            # Depth=2
            for depth in range(2, min(4, len(path_parts))):
                folder_guess = guessit(path_parts[-depth], self.OPTIONS)
                if 'title' in folder_guess:
                    path_parts[-depth] = re.sub(pattern, ' ', path_parts[-depth])
                    path_parts[-depth] = " ".join(path_parts[-depth].split())
                    guess['title'] = folder_guess['title']
                    title_depth = -depth
                    break

        if 'title' not in guess:
            print(f"Couldn't find title in filename '{path_parts[-1]}'! Guess result: {guess}")
            return path_parts

        if 'Centimeters per Second' == guess['title'] and 5 == guess.get('episode', 0):
            path_parts[title_depth] = path_parts[title_depth].replace(' 5 ', ' Five ')
            # For some reason AniList has this film in 3 parts.
            path_parts[title_depth] = path_parts[title_depth].replace('per Second', 'per Second 3')
        
        return path_parts

    # Parse the file name using guessit
    def parse_filename(self, filepath):
        path_parts = self.fix_filename(filepath.replace('\\', '/').split('/'))
        filename = path_parts[-1]
        name, season, part, year = '', '', '', ''
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
            print(f'Detected episode in episode_title. Episode: {int(guess.get('episode_title'))}')
            episode = int(guess.get('episode_title'))

        # 'episode': [86, 13] (EIGHTY-SIX), [1, 2, 3] (RANMA) lol.
        if isinstance(episode, list):
            print(f'Detected multiple episodes: {episode}. Picking last one.')
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

                    if name != '': break # If we got the name, its probable we already got season and part from the way folders are usually structured
        
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
        query = '''
            query($search: String, $year: FuzzyDateInt, $page: Int) {
                Page(page: $page) {
                    media (search: $search, type: ANIME, startDate_greater: $year) {
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
        variables = {'search': name, 'year': year or 1, 'page': 1}

        response = self.make_api_request(query, variables, self.access_token)
        if response and 'data' in response:
            seasons = response['data']['Page']['media']
            # This is the first element, which is the same as Media(search: $search)
            
            if len(seasons) == 0:
                raise Exception(f"Couldn\'t find an anime from this title! ({name})")

            anime_data = (seasons[0]['id'], seasons[0]['title']['romaji'], seasons[0]['mediaListEntry']['progress'] if seasons[0]['mediaListEntry'] is not None else -1, seasons[0]['episodes'], file_progress)
            # If the episode in the file name is larger than the total amount of episodes
            # Then they are using absolute numbering format for episodes (looking at you SubsPlease)
            # Try to guess season and episode.
            if seasons[0]['episodes'] is not None and file_progress > seasons[0]['episodes']:
                seasons = self.filter_valid_seasons(seasons)
                print('Related shows:', ', '.join(season['title']['romaji'] for season in seasons))

                anime_data = self.find_season_and_episode(seasons, file_progress)

                print(f"Final guessed anime: {next(season for season in seasons if season['id'] == anime_data[0])}") # Print data of the show
                print(f'Absolute episode {file_progress} corresponds to Anime: {anime_data[1]}, Episode: {anime_data[-1]}')
            else: 
                print(f"Final guessed anime: {seasons[0]}") # Print data of the show
            return (anime_data)
        return (None, None, None, None)
    
    # Update the anime based on file progress
    def update_episode_count(self, result):
        if result is None:
            raise Exception('Parameter in update_episode_count is null.')
        
        anime_id, anime_name, current_progress, total_episodes, file_progress = result
        
        # Only launch anilist
        if sys.argv[2] == 'launch':
            print(f'Opening AniList for "{anime_name}": https://anilist.co/anime/{anime_id}')
            webbrowser.open_new_tab(f'https://anilist.co/anime/{anime_id}')
            return result

        if current_progress is None or current_progress == -1:
            raise Exception('Failed to get current episode count. Is it on your watching/planning list?')
        
        # If its lower than the current progress, dont update.
        if file_progress <= current_progress:
            raise Exception(f'Episode was not new. Not updating ({file_progress} <= {current_progress})')
        
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

        # Handle changing "Planned to watch" animes to "Watching"
        if file_progress != total_episodes:
            variables['status'] = "CURRENT" # Set to "CURRENT" if it isn't the final episode.

        response = self.make_api_request(query, variables, self.access_token)
        if response and 'data' in response:
            updated_progress = response['data']['SaveMediaListEntry']['progress']
            print(f'Episode count updated successfully! New progress: {updated_progress}')

            return (anime_id, anime_name, updated_progress, total_episodes, file_progress)
        else:
            print('Failed to update episode count.')
            return False

def main():
    try:
        updater = AniListUpdater()
        updater.handle_filename(sys.argv[1])

    except Exception as e:
        print(f'ERROR: {e}')
        sys.exit(1)

if __name__ == '__main__':
    main()