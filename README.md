# mpv-anilist-updater

A script for MPV that automatically updates your AniList based on the file you just watched.

> [!IMPORTANT]
> By default, the anime must be set to "watching", "planning" or "rewatching" to update progress. This is done in order to prevent updating the wrong show.<br>
> **Recommendation:** Check out the configuration options in your `anilistUpdater.conf` file to customize the script to your needs. See the [Configuration](#configuration-anilistupdaterconf) section for details.<br>

> [!TIP]
> In order for the script to work properly, make sure your files are named correctly:<br>
>
> - Either the file or folder its in must have the anime title in it<br>
> - The file must have the episode number in it (absolute numbering should work)<br>
> - In case of remakes, specify the year of the remake to ensure it updates the proper one<br>
>
> To avoid the script running and making useless API calls, you can set one or more directories in the config file. See the [Configuration](#configuration-anilistupdaterconf) section below.

For any issues, you can either open an issue on here, or message me on discord (azuredblue)

## Requirements

You will need Python 3.11 or newer installed and a recent mpv (developed on 0.41). The script has no other dependencies.

## Installation

Simply git clone this repository into your mpv scripts folder, or download this repository and extract the contents into your mpv scripts folder.

If you use git clone, you can update by using `git pull` in the repository folder.

You **WILL** need an AniList access token for it to work:

1. Visit `https://anilist.co/api/v2/oauth/authorize?client_id=20740&response_type=token`
2. Authorize the app
3. Copy the token
4. Create an `anilistToken.txt` file in the `mpv-anilist-updater` folder (if not already there) and paste the token there.

This token is what allows the script to update the anime episode count and make api requests, it is not used for anything else.

## Configuration (`anilistUpdater.conf`)

> [!IMPORTANT]
> The config file is only generated after you run mpv at least once with the script installed. If the file is not created due to lack of write permissions, you can either run mpv as administrator once to generate it, or create the file manually in the appropriate directory. It is recommended to be in the `script-opts` directory.

When you first run the script, it will automatically create a configuration file called `anilistUpdater.conf` if it does not already exist. This file is typically created in your mpv `script-opts` directory where you have mpv installed. If it cannot be created there, it will try the `scripts` directory or your mpv config directory (e.g. `~/.config/mpv/` on Linux, `%APPDATA%/mpv/` on Windows).

**Config file search order:**

The script checks for the config file in the following order:

1. `script-opts` directory (recommended)
2. `scripts` directory (where the script itself is located)
3. mpv config directory (e.g. `~/.config/mpv/` or `%APPDATA%/mpv/`)

If the config file exists in more than one location, the one in the highest-priority directory (script-opts) will be used. For example, if you have a config in both `script-opts` and `scripts`, the one in `script-opts` will take precedence.

**You should edit this file to change any options.**

### Example `anilistUpdater.conf`

```ini
# Use 'yes' or 'no' for boolean options below
# Example for multiple directories (comma or semicolon separated):
# DIRECTORIES=D:/Torrents,D:/Anime
# or
# DIRECTORIES=D:/Torrents;D:/Anime
DIRECTORIES=
EXCLUDED_DIRECTORIES=
UPDATE_PERCENTAGE=85
SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE=no
UPDATE_PROGRESS_WHEN_REWATCHING=yes
SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT=yes
SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING=yes
ADD_ENTRY_IF_MISSING=no
CACHE_REFRESH_RATE=24
CACHE_MODE=NORMAL
SILENT_MODE=no
SHOW_DETECTED_ANIME=no
```

#### Option Descriptions

- **DIRECTORIES**: Comma or semicolon separated list of directories. If empty, the script works for every video. Example: `DIRECTORIES=D:/Torrents,D:/Anime`
  - Restricting directories only prevents the script from automatically updating AniList for files outside the specified directories. Manual actions using the keybinds (Ctrl+A, Ctrl+B, Ctrl+D, C) will still work for any file, regardless of its location.
- **EXCLUDED_DIRECTORIES**: Comma or semicolon separated list of directories. Useful for ignoring paths inside directories from above. Example: `EXCLUDED_DIRECTORIES=D:/Torrents/Watched,D:/Anime/Planned`
- **UPDATE_PERCENTAGE**: Number (0-100). The percentage of the video you need to watch before it updates AniList automatically. Default is `85`.
- **CACHE_REFRESH_RATE**: Number (hours). Controls how long normal cache entries remain valid. Entries are kept per title, so shows sharing a folder don't overwrite each other. Default is `24`. Higher values are recommended if you do not plan on modifying your watching list through AniList, but it might cause your cache to go stale.
- **CACHE_MODE**: `NORMAL`/`SLIDING`. Default is `NORMAL`.
  - `NORMAL`: Cache entries expire after CACHE_REFRESH_RATE except for those corrected, which last 28 days.
  - `SLIDING`: applies sliding expiration to all cache entries when they are read. Recommended with higher CACHE_REFRESH_RATE (more than a week for seasonals) values.
- **SET_COMPLETED_TO_REWATCHING_ON_FIRST_EPISODE**: `yes`/`no`. If `yes`, when watching episode 1 of a completed anime, set it to rewatching and update progress. Default is `no`.
- **UPDATE_PROGRESS_WHEN_REWATCHING**: `yes`/`no`. If `yes`, allow updating progress for anime set to rewatching. Default is `yes`.
- **SET_TO_COMPLETED_AFTER_LAST_EPISODE_CURRENT**: `yes`/`no`. If `yes`, set to COMPLETED after last episode if status was CURRENT. **Default is `yes`.**
- **SET_TO_COMPLETED_AFTER_LAST_EPISODE_REWATCHING**: `yes`/`no`. If `yes`, set to COMPLETED after last episode if status was REPEATING (rewatching). Default is `yes`.
- **ADD_ENTRY_IF_MISSING**: `yes`/`no`. If `yes`, automatically add anime to your list if it's not found in your list during search. Default is `no`. **⚠️ Warning: This can add incorrect anime to your list if the detection is inaccurate.**
- **SILENT_MODE**: `yes`/`no`. If `yes`, won't show OSD messages. Default is `no`.
- **SHOW_DETECTED_ANIME**: `yes`/`no`. If `yes`, shows the detected anime and episode when a file loads, so a wrong match can be corrected before the update. Hidden by `SILENT_MODE`. Default is `no`.

> [!NOTE]
> Boolean options are `yes` or `no`, in any letter case.

## Usage

This script has 4 keybinds:

- **Ctrl + a**: Manually updates your AniList with the current episode you are watching.
- **Ctrl + b**: Opens the AniList page of the anime you are watching on your browser. Useful to see if it guessed the anime correctly.
- **Ctrl + d**: Opens the folder where the current video is playing. Useful if you have "your own" anime library, and navigating through folders is a pain.
- **c**: Opens the [correction menu](#correction) for the current file, to search for the right anime, fix the episode, and change the status or score. Use `Ctrl+v` to paste.

The script will automatically update your AniList when the video you are watching reaches 85% completion (or the percentage you set in the config file).

### Customizing Keybinds

You can change the keybinds in your `input.conf`:

```bash
A script-binding update_anilist
B script-binding launch_anilist
D script-binding open_folder
C script-binding correct_anime_id
```

## Correction

In the case the script gets it wrong, press `c` to open the correction menu. Type a title to search AniList (or paste an AniList URL/ID), pick the right result with Up/Down, and move between fields with Tab:

- **Anime**: the title search. Results show format, year, episode count and your list status.
- **Corrected episode** (optional): the episode number within that entry, if it isn't the file's number.
- **Status**: change your list status with Left/Right.
- **Score**: shows your current score in the score system you picked on AniList (100 point, 10 point decimal, 10 point, 5 star or 3 smiley). Type it or use Left/Right to step it, and clear it with `0`.

Press Enter to apply and Esc to cancel. Setting a score needs the AniList token.

For example, if the file is "Attack On Titan - E48", search for "Attack on Titan Season 3" and set the corrected episode to 11.

The correction is remembered for the other files of that show in the folder.

## How It Works

The script reads the title, episode, season, part, year and format from the file name with its own parser, written for fansub and scene release names.

- If the file name has no title (`E12 - Welcome.mkv`), it comes from the folder. The season, part, year and format can also come from the folder (`Show/Season 2/`, `Show/OVA/`).
- Extras (openings, endings, menus, `Extras` folders), `Season 00` files and decimal episodes like `05.5` are skipped, since AniList has no episode for them.
- It then searches the title with its season and part, in your list first and then globally.
- If the episode is higher than the season's episode count, it is treated as absolute numbering and the season is found by following the sequels from the first season.

If it doesn't work properly, use the [correction menu](#correction) (`c`) to pick the right anime and episode.

---

The title can be in the file name or in a folder name:

- `Boku no Hero Academia - 152 (1080p).mkv` is detected as S7 E14 (absolute numbering).
- `86/E12 - Welcome [F1119374].mkv` gets its title from the folder. In `86 Part 2/`, the same file is episode 1 of Part 2.

To see which anime got detected, you can use `c`, `Ctrl+B` to launch the AniList page, or check mpv's console.

## MAL

For a MyAnimeList version, check out the fork [mpv-mal-updater](https://github.com/locomotiv1/mpv-mal-updater). Take into account this is a fork not maintained by me so I can't guarantee its safety. It may also be outdated or work differently.

## Credits

This script was inspired by [mpv-open-anilist-page](https://github.com/ehoneyse/mpv-open-anilist-page) by ehoneyse.
