import sys
import os
import requests

from guessit import guessit

__location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))

# Reads your AniList Access Token from the anilistToken.txt
ACCESS_TOKEN = 'xxx' # You can modify it here as well
if ACCESS_TOKEN == 'xxx':
    ACCESS_TOKEN = open(os.path.join(__location__, 'anilistToken.txt')).read() 

def get_user_id():
    query = '''
    query {
        Viewer {
            id
        }
    }
    '''

    response = requests.post(
        'https://graphql.anilist.co',
        headers={
            'Authorization': f'Bearer {ACCESS_TOKEN}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        },
        json={'query': query}
    )

    if response.status_code == 200:
        data = response.json()['data']['Viewer']
        return data['id']
    else:
        print("Failed to fetch user information")
        return None

def handle_filename(filename):
    # Attempt to determine the corresponding anime from the filename with guessit
    name = ""

    guess = guessit(filename)

    # Debugging
    print(guess)
    
    # "Title" is the name's guess from guessit
    name = guess["title"]

    # Episode guess from the title.
    if "episode" in guess:
        episode = guess["episode"]
    else:
        episode = 1 # If theres no episode count, assume 1.

    # If there is a season attached, append it to the name.
    # Exception with animes that are divided in parts
    # This has it's problems, for example, with 86 Part 2, it would search for
    # "86 1 2", which would not have results, while "86 2" would.
    # If there are any issues, try changing the name of the file using PowerRename (I can't express how good that program is)
    if "season" in guess:
        name = name + " " + str(guess["season"])

    # If there is a part attached, append it to the name.
    if "part" in guess:
        name = name + " " + str(guess["part"])

    # Get the id of the anime from AniList's api.
    anime_id = get_anime_id(name)

    # Increment the episode count
    increment_episode_count(anime_id, episode)

def get_anime_id(name):
    # Get the anime id based on the guessed name.

    ANILIST_API_URL = 'https://graphql.anilist.co'

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
        return response.json()["data"]["Media"]["id"]
    else:
        raise Exception("Query failed!")

    return None


def get_episode_count(id):
    # Get the episode count to avoid updating the anime to a lower episode count
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
       }
     }
   }
    '''

    variables = {
        "mediaId": id,
        "userId": get_user_id() # Your user id, not sure if there's a way to get your current progress without it. I tried it and it kept saying "Completed" on status.
    }

    response = requests.post(
        'https://graphql.anilist.co',
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
        print(response.json())
        return response.json()['data']['MediaList']['progress']
    elif response.status_code == 404: # Happens if you don't have that anime on your list.
        raise Exception("ANIME NOT IN USER\'S LIST. ABORTING")
    else:
        raise Exception("Error while trying to get episode count.")


def increment_episode_count(id, progress):

    current_progress = get_episode_count(id)
    if current_progress is None:
        return
    # progress is the episode gotten from the filename
    if progress <= current_progress:
        raise Exception(f"Episode was not new. Not updating ({progress} <= {current_progress})")

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
        "mediaId": id,
        "progress": progress
    }

    # Send the request to AniList
    response = requests.post(
        'https://graphql.anilist.co',
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
        print(f"Episode count updated successfully! New progress: {updated_progress}")
    else:
        print(f"Failed to update episode count: {response.status_code}")
        print(response.json())



if __name__ == "__main__":
    try:
        handle_filename(sys.argv[1])
    except Exception as e:
        print("ERROR: {}".format(e))
        sys.exit(1)