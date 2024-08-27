# mpv-anilist-updater
A script for MPV that automatically updates your AniList based on the file you just watched. It will not update if the anime is not in your user's library or if the episode you are watching isnt newer than your current progress count.

KEEP IN MIND, it all depends on how the file is named. Guessit, unfortunately, cannot do magic. If it does not work in some of your torrented anime, try using PowerRename to change it into a better format (`{name} {part?} - {S\dE\d+}`)

This is a personal project, not sure how efficient it is.

## Requirements
You will need Python 3 installed, as well as the libraries `guessit` and `requests`:
```bash
pip install guessit requests
```

## Installation
Copy the `.lua` and `.py` files into your mpv scripts folder.

You **WILL** need an AniList token for it to work, heres how to get one:
  1. Go to AniList and click on settings.
  2. Go to the `Developer` section and click on `Create a new client`
  3. There, simply put the name you wish (`MPV Integration`) and a Redirect URL (doesnt matter, i chose `http://localhost`)
  4. After that, simply copy your Client ID and go to `https://anilist.co/api/v2/oauth/authorize?client_id={client_id}&response_type=token`, replacing `{client_id}` with yours.
  5. Then, authorize the app, and you will be redirected to the URL you put before. The Access Token will be on the url: `https://localhost/#access_token={token}&token_type=Bearer&expires_in=31536000`

After that, you can either create a `anilistToken.txt` file in the scripts folder, or modify the `.py` file (line 10).

## Usage
The script will automatically update your anilist when the video you are watching reaches 85% completion. You can also use the keybind `ctrl + a` to do it manually.
You can change this keybind in your input.conf:
```bash
A script-binding update_anilist
```

Or in the `.lua` file:
```lua
mp.add_key_binding('ctrl+a', 'update_anilist', update_anilist)
```

## Credits
This script was inspired by [mpv-open-anilist-page](https://github.com/ehoneyse/mpv-open-anilist-page) by ehoneyse.
