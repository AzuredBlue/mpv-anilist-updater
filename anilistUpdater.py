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
    # Here, we are dealing with filename, which is the absolute path of the file e are playing
    # Split will divide into the diferent directories, you can't have a directory that includes "\"
    filename = filename.split("\\")

    name = ""
    # Attempt to guess with the filename
    guess = guessit(filename[-1], {'type': 'episode'})

    # Debugging
    print(guess)
    keys = list(guess.keys())

    # Usually, releases are formated [Release Group] Title - S01EX
    # If the episode index is 0, that would mean that the episode is before the title in the filename
    # Which is a horrible way of formatting it, so assume its wrong
    # If its 1, then the title is probably 0, so its okay
    # If its >2, theres probably a Release Group and Title / Season / Part, so its good
    # Episode guess from the title.

    if "episode" in guess:
        episode = guess["episode"] # For cases in which the episode is 11.5, it will take it as episode 11 and 
                                   # therefore not updating it, since you watch episode 11 first.
        episode_index = keys.index("episode")
        print("EPISODE INDEX: " + str(episode_index))
    else:
        episode = 1 # If theres no episode count, assume 1.
        episode_index = 1 # If it has no episode, its probably a movie and it should be okay
    
    
    # "Title" is the name's guess from guessit
    if "title" in guess and episode_index > 0:
        name = guess["title"]
        
        # If there are any issues, try changing the name of the file using PowerRename (I can't express how good that program is)
        if "season" in guess:
            name = name + " " + str(guess["season"])

        # If there is a part attached, append it to the name.
        if "part" in guess:
            name = name + " " + str(guess["part"])

    else:
        # If it isnt in the name of the file, try to guess using the name of the folder it is stored in
        guess = guessit(filename[-2])
        print(guess)
        name = guess["title"]

        if "season" in guess:
            name = name + " " + str(guess["season"])

        if "part" in guess:
            name = name + " " + str(guess["part"])

    # Adding the season AND part sometimes has its problems
    # See EIGHTY SIX SEASON 1 PART 2:
    # "EIGHTY SIX 1 2" does not give results searching, however
    # "EIGHTY SIX 2" does

    # Get the id of the anime from AniList's api.
    anime_id = get_anime_id(name)
    
    print(name)
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