import sys
import os
import webbrowser
import requests
from guessit import guessit
__location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))
TXTPATH = os.path.join(__location__, 'anilistToken.txt')

ANILIST_API_URL = 'https://graphql.anilist.co'
ACCESS_TOKEN = 'xxx' # You can modify it here as well

# Reads your AniList Access Token from the anilistToken.txt
if ACCESS_TOKEN == 'xxx':
    txt = open(TXTPATH).read().replace('\n', '')
    if ':' in txt:
        ACCESS_TOKEN = txt.split(':')[1]
    else:
        ACCESS_TOKEN = txt

def get_user_id():
    # Cache the user id
    txt = ''
    try:
        txt = open(TXTPATH).read().replace('\n', '')
    except Exception as e:
        print('Error trying to read file: ' + str(e))

    if ':' in txt:
        return int(txt.split(':')[0])
    else:
        query = '''
        query {
            Viewer {
                id
            }
        }
        '''

        response = requests.post(
            ANILIST_API_URL,
            headers={
                'Authorization': f'Bearer {ACCESS_TOKEN}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            json={'query': query}
        )

        if response.status_code == 200:
            user_id = response.json()['data']['Viewer']['id']
            try:
                with open(TXTPATH, 'r') as file:
                    existing_content = file.read()

                with open(TXTPATH, 'w') as file:
                    file.write(str(user_id) + ':' + existing_content)
            except Exception as e:
                print('Error trying to read file: ' + str(e))

            return user_id
        else:
            print('Failed to fetch user information')
            return None
    
# Only gets ran when we need to figure out the season of an anime with absolute numbering
def get_all_seasons(anime_name):
    query = '''
    query ($search: String, $page: Int) {
        Page(page: $page) {
            media(search: $search, type: ANIME) {
                id
                title {
                    romaji
                }
                season
                seasonYear
                episodes
                format
                duration
            }
        }
    }
    '''

    variables = {
        'search': anime_name,
        'page': 1
    }

    response = requests.post(
        ANILIST_API_URL,
        json={'query': query, 'variables': variables}
    )

    if response.status_code == 200:
        data = response.json()
        anime_seasons = []

        for media in data['data']['Page']['media']:
            if media['format'] == 'TV' and media['duration'] > 21:  # Only include TV format and longer than 21 minutes per episode
                anime_seasons.append({
                    'title': media['title']['romaji'],
                    'id': media['id'],
                    'episodes': media['episodes'],
                    'seasonYear': media['seasonYear'],
                    'season': media['season']
                })
        
        # Needed to sort the seasons correctly.
        season_order = {
            'WINTER': 1,
            'SPRING': 2,
            'SUMMER': 3,
            'FALL': 4
        }

        # Sort by seasonYear and season
        anime_seasons_sorted = sorted(
            anime_seasons,
            key=lambda x: (x['seasonYear'], season_order.get(x['season'], 5))
        )

        return anime_seasons_sorted
    else:
        raise Exception(f'Query failed with status code {response.status_code}: {response.text}')

# Only gets ran when we need to figure out the season of an anime with absolute numbering
def find_season_and_episode(anime_name, absolute_episode):
    try:
        seasons = get_all_seasons(anime_name)
        
        # Initialize episode accumulation
        accumulated_episodes = 0
        
        for season in seasons:
            season_episodes = season['episodes']
            if accumulated_episodes + season_episodes >= absolute_episode:
                title = season['title']
                id = season['id']
                relative_episode = absolute_episode - accumulated_episodes
                return (title, id, relative_episode)
            
            accumulated_episodes += season_episodes
        
        return None  # If the absolute episode number is higher than the total episodes

    except Exception as e:
        print(f'An error occurred: {e}')
        return None

def handle_filename(filename):
    # Attempt to determine the corresponding anime from the filename with guessit
    # Here, we are dealing with filename, which is the absolute path of the file e are playing
    # Split will divide into the diferent directories, you can't have a directory that includes "\"
    if filename[0] == '/':
        filename = filename.split('/')
    else:
        filename = filename.split('\\')

    name = ''
    season = ''
    part = ''

    season_index = -1
    episode_index = -1
    
    # Attempt to guess with the filename
    guess = guessit(filename[-1], {'type': 'episode'})
    # Debugging
    print('File name guess: ' + str(guess))
    keys = list(guess.keys())

    # Episode guess from the title.
    # Usually, releases are formated [Release Group] Title - S01EX

    # If the episode index is 0, that would mean that the episode is before the title in the filename
    # Which is a horrible way of formatting it, so assume its wrong

    # If its 1, then the title is probably 0, so its okay. (Unless season is 0)
    # Really? What is the format "S1E1 - {title}"? That's almost psycopathic.

    # If its >2, theres probably a Release Group and Title / Season / Part, so its good

    if 'episode' in guess:
        episode = guess['episode'] # For cases in which the episode is 11.5, it will take it as episode 11 and 
                                   # therefore not updating it, since you watch episode 11 first.
        episode_index = keys.index('episode')
        # print("EPISODE INDEX: " + str(episode_index))
    else:
        episode = 1 # If theres no episode count, assume 1.
        episode_index = 1 # If it has no episode, its probably a movie and it should be okay
    
    # Store the "season" and "part" of the original file name if they have.
    if 'season' in guess:
        season_index = keys.index('season')
        season = str(guess['season'])

    if 'part' in guess:
        part = str(guess['part'])
    
    # "Title" is the name's guess from guessit
    # If the episode index > 0 and season index > 0, its safe to assume that the title is in the file name
    if 'title' in guess and (episode_index > 0 and (season_index > 0 or season_index == -1)):
        name = guess['title']
    else:
        # If it isnt in the name of the file, try to guess using the name of the folder it is stored in
        guess = guessit(filename[-2], {'type': 'episode'})
        print('Folder guess:' + str(guess))
        name = guess['title']

        # Add if they werent in the original file
        if not season and 'season' in guess:
            season = str(guess['season'])

        if not part and 'part' in guess:
            part = str(guess['part'])

    if season:
        name = name + ' ' + season
    
    if part:
        name = name + ' ' + part
    
    # Adding the season AND part sometimes has its problems
    # See EIGHTY SIX SEASON 1 PART 2:
    # "EIGHTY SIX 1 2" does not give results searching, however
    # "EIGHTY SIX 2" does

    print('Guessed name: ' + name)

    # Get the id of the anime from AniList's api.
    anime_id = get_anime_id(name)
    
    # Increment the episode count
    increment_episode_count(anime_id, episode, name)

def get_anime_id(name):
    # Get the anime id based on the guessed name.

    query = '''
    query ($searchStr: String) { 
        Media (search: $searchStr, type: ANIME) {
            id
            siteUrl
        }
    }
    '''

    variables = {
        'searchStr': name
    }

    response = requests.post(ANILIST_API_URL, json={'query': query, 'variables': variables})

    if response.status_code == 200:
        # Print the whole response for debugging.
        print(response.json())
        return response.json()['data']['Media']['id']
    else:
        raise Exception('Query failed!')

def get_episode_count(id):
    # Get the episode count to avoid updating the anime to a lower episode count
    # Returns an array [progress, totalEpisodes]
    query = '''
    query ($mediaId: Int, $userId: Int) {
     MediaList(mediaId: $mediaId, userId: $userId) {
       id
       mediaId
       status
       progress
       media {
         title {
           romaji
           english
         }
         episodes
       }
     }
   }
    '''

    variables = {
        'mediaId': id,
        'userId': get_user_id() # Your user id, not sure if there's a way to get your current progress without it. I tried it and it kept saying "Completed" on status.
    }

    response = requests.post(
        ANILIST_API_URL,
        json={'query': query, 'variables': variables}
    )

    if response.status_code == 200:
        print(response.json())
        return [response.json()['data']['MediaList']['progress'], response.json()['data']['MediaList']['media']['episodes']]
    elif response.status_code == 404: # Happens if you don't have that anime on your list.
        
        # Still try to launch the anilist, might be wrong if its in absolute numbering
        # For it to work properly, you'd need the anime on your watch list
        if sys.argv[2] == 'launch':
            webbrowser.open_new_tab('https://anilist.co/anime/' + str(id))

        raise Exception('ANIME NOT IN USER\'S LIST. ABORTING')
    else:
        raise Exception('Error while trying to get episode count.')


def increment_episode_count(id, file_progress, name):

    [current_progress, totalEpisodes] = get_episode_count(id)

    if current_progress is None:
        return
    
    # If the episode on the file is more than the total number of episodes, they are using absolute formatting (Ex. Jujutsu Kaisen - 46 = Jujutsu Kaisen S2E22)
    if file_progress > totalEpisodes:
        print('Episode number is in absolute value. Trying to convert to season and episode.')
        result = find_season_and_episode(name, file_progress)
        if result:
            title, anime_id, episode = result
            print(f'Absolute episode {file_progress} corresponds to Anime: {title}, Episode: {episode}')
            increment_episode_count(anime_id, episode, title)
            return

        
    # Make sure its after it was converted from absolute numbering to Season and Episode
    if sys.argv[2] == 'launch':
        webbrowser.open_new_tab('https://anilist.co/anime/' + str(id))
        return
    
    # If the episode on the file name is less than your current progress, dont update
    if file_progress <= current_progress:
        raise Exception(f'Episode was not new. Not updating ({file_progress} <= {current_progress})')

    # Prepare the GraphQL mutation query
    query = '''
    mutation ($mediaId: Int, $progress: Int) {
        SaveMediaListEntry (mediaId: $mediaId, progress: $progress) {
            id
            progress
        }
    }
    '''

    variables = {
        'mediaId': id,
        'progress': file_progress
    }

    # Send the request to AniList
    response = requests.post(
        ANILIST_API_URL,
        headers={
            'Authorization': f'Bearer {ACCESS_TOKEN}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        },
        json={
            'query': query,
            'variables': variables,
        }
    )

    if response.status_code == 200:
        updated_progress = response.json()['data']['SaveMediaListEntry']['progress']
        print(f'Episode count updated successfully! New progress: {updated_progress}')
    else:
        print(f'Failed to update episode count: {response.status_code}')
        print(response.json())

if __name__ == '__main__':
    try:
        handle_filename(sys.argv[1])
    except Exception as e:
        print('ERROR: {}'.format(e))
        sys.exit(1)