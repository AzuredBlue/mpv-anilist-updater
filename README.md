# mpv-anilist-updater
A script for MPV that automatically updates your AniList based on the file you just watched. It will not update if the anime is not in your user's library or if the episode you are watching isnt newer than your current progress count.

KEEP IN MIND, it all depends on how the file is named. Guessit, unfortunately, cannot do magic. 
If the file does not have the anime name, make sure the folder containing that anime does have the name.

This should work on most torrented anime formats, including those that use absolute formatting:

`[SubsPlease] Boku no Hero Academia - 152 (1080p) [AAC292E7].mkv` will be detected as S7 E14 and updated accordingly.

`E12 - Welcome [F1119374].mkv` will work if the folder that it is in has `86` in the name. If it has `86 Part 2` then it should be `Episode 1`

This is a personal project, not sure how efficient it is.

## Requirements
You will need Python 3 installed, as well as the libraries `guessit` and `requests`:
```bash
pip install guessit requests
```

## Installation
Copy the `.lua` and `.py` files into your mpv scripts folder.

You **WILL** need an AniList access token for it to work:
  1. Visit `https://anilist.co/api/v2/oauth/authorize?client_id=20740&response_type=token`
  2. Then, authorize the app, and you will be redirected to a localhost url
  3. Copy the token from the url (`https://localhost/#access_token= {token} &token_type=Bearer&expires_in=31536000`)

After that, you can either create a `anilistToken.txt` file in the scripts folder, or modify the `.py` file (line 12).

## Usage
This script has 2 keybinds:
  - Ctrl + A: Manually updates your AniList with the current episode you are watching.
  - Ctrl + B: Opens the AniList page of the anime you are watching on your browser. Useful to see if it guessed the anime correctly.

The script will automatically update your AniList when the video you are watching reaches 85% completion.

You can change the keybinds in your input.conf:
```bash
A script-binding update_anilist
B script-binding launch_anilist
```

Or in the `.lua` file:
```lua
mp.add_key_binding('ctrl+a', 'update_anilist', function()
    update_anilist("update")
end)

mp.add_key_binding('ctrl+b', 'launch_anilist', function()
    update_anilist("launch")
end)
```

## How It Works
The script uses Guessit to try to get as much information as possible from the file name.

If the "episode" and "season" guess are before the title, it will consider that title wrong and try to get the title from the name of the folder it is in.

If the torrent file has absolute numbering (looking at you, SubsPlease), it will try to guess the season and episode by:
  1. Searching for the anime name on the AniList API.
  2. Get all results with a similar name, whose format are `TV` and the duration greater than 21 minutes.
  3. Sort them based on release date.
  4. Get the season based on the absolute episode number

It is not a flawless method. It won't work properly if the anime has seasons as ONA's. If it doesn't work properly, consider 
changing the episode number to the normal format yourself, or simply give up on that series.

## Credits
This script was inspired by [mpv-open-anilist-page](https://github.com/ehoneyse/mpv-open-anilist-page) by ehoneyse.
