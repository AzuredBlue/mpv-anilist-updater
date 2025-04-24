# mpv-anilist-updater

A script for MPV that automatically updates your AniList based on the file you just watched.

> [!IMPORTANT]
> The anime must be set to "watching" or "planning". This is done in order to prevent updating the wrong show.<br>
> It will not update if you are rewatching an episode.

> [!TIP]
> In order for the script to work properly, make sure your files are named correctly:<br>
>
> - Either the file or folder its in must have the anime title in it<br>
> - The file must have the episode number in it (absolute numbering should work)<br>
> - In case of remakes, specify the year of the remake to ensure it updates the proper one<br>
>
> To avoid the script running and making useless API calls, you can set one or more directories in `main.lua`, where it will work

For any issues, you can either open an issue on here, or message me on discord (azuredblue)

## Requirements

You will need Python 3 installed, as well as the libraries `guessit` and `requests`:

```bash
pip install guessit requests
```

## Installation

Simply download the `anilistUpdater` folder and put it in your mpv scripts folder, or download the contents and make the folder yourself.

You **WILL** need an AniList access token for it to work:

1. Visit `https://anilist.co/api/v2/oauth/authorize?client_id=20740&response_type=token`
2. Authorize the app
3. Copy the token
4. Create an `anilistToken.txt` file in the `anilistUpdater` folder (if not already there) and paste the token there.

This .txt file is also used to cache your AniList user id and to cache recently seen shows, avoiding extra API Calls.
This token is what allows the script to update the anime episode count and make api requests, it is not used for anything else.

## Usage

This script has 3 keybinds:

- Ctrl + A: Manually updates your AniList with the current episode you are watching.
- Ctrl + B: Opens the AniList page of the anime you are watching on your browser. Useful to see if it guessed the anime correctly.
- Ctrl + D: Opens the folder where the current video is playing. Useful if you have "your own" anime library, and navigating through folders is a pain.

The script will automatically update your AniList when the video you are watching reaches 85% completion.

You can change the keybinds in your input.conf:

```bash
A script-binding update_anilist
B script-binding launch_anilist
D script-binding open_folder
```

Or in the `.lua` file:

```lua
mp.add_key_binding('ctrl+a', 'update_anilist', function()
    update_anilist("update")
end)

mp.add_key_binding('ctrl+b', 'launch_anilist', function()
    update_anilist("launch")
end)

mp.add_key_binding('ctrl+d', 'open_folder', open_folder)
```

### Specifying Directories

To limit the script to only work on files in certain directories, open `main.lua` and set the `DIRECTORIES` table near the top of the file. For example:

```lua
DIRECTORIES = {"D:/Torrents", "D:/Anime"}
```

- If you leave the table empty (`DIRECTORIES = {}`), the script will work for every video you watch with mpv.
- If you specify one or more directories, the script will only trigger for files whose path starts with any of those directories.
> [!NOTE]
> Restricting directories only prevents the script from automatically updating AniList for files outside the specified directories. Manual actions using the keybinds (Ctrl+A, Ctrl+B, Ctrl+D) will still work for any file, regardless of its location.

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

## FAQ (Probably)

**Q: On what formats does it work?**

A: It should work on most formats as long as the name is present in the file itself or the folder name.

`[SubsPlease] Boku no Hero Academia - 152 (1080p) [AAC292E7].mkv` will be detected as S7 E14 and updated accordingly.

`E12 - Welcome [F1119374].mkv` will work if the folder that it is in has `86` in the name. If it has `86 Part 2` then it should be `Episode 1`

If it does not, try changing the name of the file / folder, so the search has a better chance at finding it

**Q: Can I see which anime got detected before it updates?**

A: Ctrl + B will launch the AniList page of the anime it detects. To see more debug info launch via command line with `mpv file.mkv` or press the `\`` keybind to open the console.

**Q: Can it wrongfully update my anime?**

A: No, AniList's API does not allow updating an anime which isnt on your watch list. If it didn't detect your anime correctly, then it will
simply error.

**Q: It does not work with X format. What do I do?**

A: You can try launching the file through the command line with `mpv file.mkv` or opening the console through the keybind \` and see `Guessed name: X`. Try changing the file's name or folder so it has
a better chance at guessing the anime. If it still doesn't work, try opening a GitHub issue or messaging me on discord (azuredblue).

## Credits

This script was inspired by [mpv-open-anilist-page](https://github.com/ehoneyse/mpv-open-anilist-page) by ehoneyse.
