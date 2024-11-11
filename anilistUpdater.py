import sys
import os
import webbrowser
import requests
from guessit import guessit

class AniListUpdater:
    ANILIST_API_URL = 'https://graphql.anilist.co'
    TOKEN_PATH = os.path.join(os.path.dirname(__file__), 'anilistToken.txt')

    # Load token and user id
    def __init__(self):
        self.access_token = self.load_access_token() # Replace token here if you don't use the .txt
        self.user_id = self.get_user_id()

    # Load token from anilistToken.txt
    def load_access_token(self):
        try:
            with open(self.TOKEN_PATH, 'r') as file:
                content = file.read().strip()
                return content.split(':')[1] if ':' in content else content
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

    # Function to make an api request to AniList's api
    def make_api_request(self, query, variables=None, access_token=None):
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        if access_token:
            headers['Authorization'] = f'Bearer {access_token}'
        
        response = requests.post(self.ANILIST_API_URL, json={'query': query, 'variables': variables}, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            print(f'API request failed: {response.status_code} - {response.text}')
            return None

    @staticmethod
    def season_order(season):
        return {'WINTER': 1, 'SPRING': 2, 'SUMMER': 3, 'FALL': 4}.get(season, 5)

    # Finds the season and episode of an anime with absolute numbering
    def find_season_and_episode(self, anime_name, absolute_episode, seasons):
        accumulated_episodes = 0
        for season in seasons:
            season_episodes = season['episodes']
            if accumulated_episodes + season_episodes >= absolute_episode:
                return (
                    season['id'],
                    season['title']['romaji'],
                    season['mediaListEntry']['progress'] if season['mediaListEntry'] is not None else None,
                    season['episodes'],
                    absolute_episode - accumulated_episodes
                )
            accumulated_episodes += season_episodes
        return (None, None, None, None, None)

    def handle_filename(self, filename):
        file_info = self.parse_filename(filename)
        result = self.get_anime_info_and_progress(file_info['name'], file_info['episode'], file_info['year'])

        self.update_episode_count(result, file_info['episode'])

    # Hardcoded exceptions to fix detection
    # Easier than just renaming my files 1 by 1 on Qbit
    # Every exception I find will be added here
    def fix_filename(self, filename):
        guess = guessit(filename, {'type': 'episode'}) # Simply easier for fixing the filename if we have what it is detecting.

        # Ranma 1/2 1 detected as episodes [1,2]
        if 'Ranma' in guess['title'] and len(guess['episode']) > 1:
            filename = filename.replace('1_2', '').replace('1/2', '')

        # Chi - Chikyuu no Undou ni Tsuite detected as 'Chi'
        if 'Chi' == guess['title']:
            filename = filename.replace(' - ', ' ')

        # Bleach TYBW, TYBW gets detected as alternative_title.
        # This doesn't fix some, you'd have to manually rename the files to Bleach Thousand Year Blood War E${i}
        if 'Bleach' == guess['title'] and 'alternative_title' in guess and('Thousand Year Blood War' in guess['alternative_title'] or 'Sennen Kessen-hen' in guess['alternative_title']):
            filename = filename.replace('-', ' ')

        if 'language' in guess:
            # Oshi No Ko for some reason gets detected as "language" : "ko" for some reason.
            # You are allowed to judge the solution, but it works.
            if guess['language'] == 'ko' and guess['title'] == 'Oshi no':
                filename = filename.replace("Oshi no Ko", "Oshi noKo")

        return filename

    # Parse the file name using guessit
    def parse_filename(self, filepath):
        path_parts = filepath.replace('\\', '/').split('/')
        filename = self.fix_filename(path_parts[-1])
        folder_name = path_parts[-2] if len(path_parts) > 1 else ''

        name, season, part, year = '', '', '', ''
        episode = 1

        # First, try to guess from the filename
        guess = guessit(filename, {'type': 'episode'})
        print('File name guess: ' + str(guess))

        # Episode guess from the title.
        # Usually, releases are formated [Release Group] Title - S01EX
    
        # If the episode index is 0, that would mean that the episode is before the title in the filename
        # Which is a horrible way of formatting it, so assume its wrong
    
        # If its 1, then the title is probably 0, so its okay. (Unless season is 0)
        # Really? What is the format "S1E1 - {title}"? That's almost psycopathic.
    
        # If its >2, theres probably a Release Group and Title / Season / Part, so its good

        episode = guess.get('episode', 1)
        season = str(guess.get('season', ''))
        part = str(guess.get('part', ''))
        year = str(guess.get('year', ''))

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
            folder_guess = guessit(folder_name, {'type': 'episode'})
            print('Folder guess: ' + str(folder_guess))
            
            name = str(folder_guess.get('title', ''))
            season = season or str(folder_guess.get('season', ''))
            part = part or str(folder_guess.get('part', ''))
            year = year or str(folder_guess.get('year', ''))   
        
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

    def  get_anime_info_and_progress(self, name, file_progress, year=None, ):
        # TODO: Use Page to get all the seasons at the same time and only return the first result 
        if year:
            query = '''
            query($search: String, $year: Int, $page: Int) {
                Page(page: $page) {
                    media (search: $search, type: ANIME, seasonYear: $year) {
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
            variables = {'search': name, 'year': year, 'page': 1}
        else:
            query = '''
            query($search: String, $page: Int) {
                Page(page: $page) {
                    media (search: $search, type: ANIME) {
                        id
                        title { romaji }
                        season
                        seasonYear
                        episodes
                        format
                        duration
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
            variables = {'search': name, 'page': 1}

        response = self.make_api_request(query, variables, self.access_token)
        if response and 'data' in response:
            seasons = response['data']['Page']['media']
            print(seasons[0])

            # This is the first element, which is the same as Media(search: $search)
            anime_data = (seasons[0]['id'], seasons[0]['title']['romaji'], seasons[0]['mediaListEntry']['progress'], seasons[0]['episodes'])

            # If the episode in the file name is larger than the total amount of episodes
            # Then they are using absolute numbering format for episodes (looking at you SubsPlease)
            # Try to guess season and episode.
            if seasons[0]['episodes'] is not None and file_progress > seasons[0]['episodes']:
                
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

                anime_data = self.find_season_and_episode(seasons[0]['title']['romaji'], file_progress, seasons)
                print(f'Absolute episode {file_progress} corresponds to Anime: {anime_data[1]}, Episode: {anime_data[-1]}')

                file_progress = anime_data[-1] # Update file_progress
                anime_data = anime_data[:-1] # Remove new_progress, not needed

            return (anime_data)
        return (None, None, None, None)
    
    # Update the anime based on file progress
    def update_episode_count(self, result, file_progress):
        if result is None:
            raise Exception('Parameter in update_episode_count is null.')
        
        anime_id, anime_name, current_progress, total_episodes = result

        # 'episode': [86, 13], lol.
        # I don't know of a way to actually fix this in fix_filename, since it takes episode_title as title, and 86 as the episode.
        if isinstance(file_progress, list):
            file_progress = min(file_progress)

        # Only launch anilist
        if sys.argv[2] == 'launch':
            print(f'Opening AniList for "{anime_name}": https://anilist.co/anime/{anime_id}')
            webbrowser.open_new_tab(f'https://anilist.co/anime/{anime_id}')
            return

        if current_progress is None:
            raise Exception('Failed to get current episode count. Is it on your watching/planning list?')
        
        # If its lower than the current progress, dont update.
        if file_progress <= current_progress:
            raise Exception(f'Episode was not new. Not updating ({file_progress} <= {current_progress})')
        
        # Handle changing "Planned to watch" animes to "Watching"
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

        if file_progress != total_episodes:
            variables['status'] = "CURRENT" # Set to "CURRENT" if it isn't the final episode.

        response = self.make_api_request(query, variables, self.access_token)
        if response and 'data' in response:
            updated_progress = response['data']['SaveMediaListEntry']['progress']
            print(f'Episode count updated successfully! New progress: {updated_progress}')
        else:
            print('Failed to update episode count.')

def main():
    try:
        updater = AniListUpdater()
        updater.handle_filename(sys.argv[1])
    except Exception as e:
        print(f'ERROR: {e}')
        sys.exit(1)

if __name__ == '__main__':
    main()