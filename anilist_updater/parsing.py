"""Turn a media path into a title, episode, year and format: the filename first, then its folders for a title."""

import logging
import re
from dataclasses import dataclass, replace

from .errors import ParseError
from .models import FileInfo
from .release import VIDEO_EXTENSIONS, Release, fold_title, parse_release

log = logging.getLogger(__name__)

# "Studio Ghibli - Movie 00 - Nausicaa": the film's own title comes after the number
_MOVIE_SUBTITLE = re.compile(r"\bMovie\s*\d{1,2}\s*-\s*(?P<subtitle>[^\W\d_][^\[\(]+)", re.IGNORECASE)


_ARC_FOLDER = re.compile(r"Season\s*\d{1,2}\s*(?:\(Part[^)]*\))?\s*-?\s*(?P<arc>[^\W\d_][^\[\(]*)", re.IGNORECASE)


@dataclass(slots=True)
class _Details:
    """Season, part, year and format, filled from the filename first and the folders after."""

    season: int | None = None
    part: int | None = None
    year: int | None = None
    media_format: str | None = None

    def fill_from(self, release: Release) -> None:
        if self.season is None:
            self.season = release.season
        self.part = self.part or release.part
        self.year = self.year or release.year
        self.media_format = self.media_format or release.media_format


def _split_path(path: str) -> list[str]:
    """Split on separators, but not inside brackets ("[720p/MKV]") or spaced out ("Title / Other title")."""
    parts, depth, start = [], 0, 0
    path = path.replace("\\", "/")
    for i, char in enumerate(path):
        if char in "[({":
            depth = min(depth + 1, 1)
        elif char in "])}":
            depth = max(depth - 1, 0)
        elif char == "/" and not depth and not (path[i - 1 : i].isspace() and path[i + 1 : i + 2].isspace()):
            parts.append(path[start:i])
            start = i + 1
    return [*parts, path[start:]]


def _title_from_folders(parts: list[str], details: _Details) -> str:
    """Look for the title in the parent folder, then the grandparent."""
    number = ""  # "Show/1/" is the season's number, unless it is all there is or the file says another ("86/")
    for folder in reversed(parts[:-1][-2:]):
        if re.fullmatch(r"\d|0\d", folder.strip()) and details.season in {None, int(folder)}:
            number = number or folder.strip()
            continue
        release = parse_release(folder, folder=True)
        log.info("Folder guess: %s -> %s", folder, release)

        details.fill_from(release)
        if release.title:
            if number and details.season is None:
                details.season = int(number)
            return release.title
    return number


def _skip_reason(release: Release, parent: Release | None) -> str | None:
    """Why the file isn't an episode, from its name or from a parent folder like "Extras" or "Season 00"."""
    if release.skip or parent is None:
        return release.skip
    if parent.skip:
        return parent.skip
    if parent.season == 0 and not parent.title and not (release.media_format or parent.media_format):
        return "a season 0 special"
    return None


def _alt_title(name: str, release: Release) -> str | None:
    """The other name of the show in the file: a bracketed one, or the title after "Movie 01 -"."""
    stem, _, extension = name.rpartition(".")
    if match := _MOVIE_SUBTITLE.search(stem if extension.lower() in VIDEO_EXTENSIONS else name):
        alt = match["subtitle"]
    else:
        alt = next((a for a in release.alt_titles if re.search(r"[^\W\d_]{3}", a)), "")
    return " ".join(re.split(r"[^\w']+", alt.replace("`", "'"))).strip(" '") or None


def _folder_title(parent: Release | None, release: Release) -> str | None:
    """The parent folder's title when it goes on from the file's own ("Initial D Fifth Stage/Initial D - S05E07")."""
    if not (parent and parent.title and release.title):
        return None
    title = re.sub(
        r"^\d{1,2}\s+(?=[^\W\d])", "", parent.title
    )  # "09 - Initial D Fifth Stage": the index orders folders
    return title if title.casefold().startswith(f"{release.title.casefold()} ") else None


def _arc(parts: list[str], release: Release) -> str | None:
    """The arc named by the season folder of a file that has its own title."""
    if len(parts) < 2 or not release.title:
        return None
    match = _ARC_FOLDER.fullmatch(parts[-2].strip())
    return " ".join(re.split(r"[^\w']+", match["arc"])).strip(" '") or None if match else None


def _named_special(release: Release, parent: Release | None) -> Release | None:
    """The special of "Show - S00E01" inside "Show - Subtitle/": the folder names it, and AniList lists it on its own."""
    if release.season != 0 or not release.skip or not (parent and parent.title and release.title):
        return None
    own, folder = fold_title(release.title), fold_title(parent.title)
    if folder == own or not folder.startswith(own):
        return None
    return replace(release, title=parent.title, season=None, media_format="SPECIAL", skip=None)


def _season_folder(parts: list[str], parent: Release | None, release: Release) -> Release | None:
    """The folder whose season and part apply to a file with a title of its own.

    That is the show's folder ("Show S2/") or a bare "Season 2/" inside it. A folder for another show, or for an
    arc the file names itself, says nothing about this file.
    """
    if parent is None or not release.title:
        return None
    show = parent
    if not parent.title and len(parts) > 2:
        show = parse_release(parts[-3], folder=True)
    return parent if fold_title(show.title or "") == fold_title(release.title) else None


def parse_path(path: str) -> FileInfo:
    """Extract title, episode, year and format from a media path.

    Args:
        path (str): Path to the video, or the media title for streams.

    Returns:
        FileInfo: What the path says about the media.

    Raises:
        ParseError: If the file isn't an episode to record, or neither the filename nor its folders have a title.
    """
    parts = _split_path(path)
    name = parts[-1]
    release = parse_release(name)
    log.info("File name guess: %s -> %s", name, release)
    parent = parse_release(parts[-2], folder=True) if len(parts) > 1 else None
    release = _named_special(release, parent) or release
    if skip := _skip_reason(release, parent):
        raise ParseError(f"'{name}' is {skip}. Not updating.")

    details = _Details(release.season, release.part, release.year, release.media_format)
    if parent and not parent.title:  # "OVA/", "Specials/"
        details.media_format = details.media_format or parent.media_format
    if folder := _season_folder(parts, parent, release):
        # A filename with a title numbers its own episodes; a season from the folder would count them twice
        details.year = details.year or folder.year
        details.media_format = details.media_format or folder.media_format
    # An episode or season first ("E12 - Welcome") means the filename has no title of its own
    title = release.title or _title_from_folders(parts, details)
    if not title and release.episode_title:
        log.info("No folder has a title; using the text after the episode: %s", release.episode_title)
        title = release.episode_title
    if not title:
        raise ParseError(f"Couldn't find title in filename '{name}'! Guess result: {release}")

    episode = 1 if release.episode is None else release.episode
    # A season taken from a folder may not fit the episode ("Show S3/Show - 38" is absolute)
    inherited = (details.season, details.part) != (release.season, release.part)
    info = FileInfo(
        title,
        episode,
        details.season,
        details.part,
        details.year,
        details.media_format,
        folder_season=inherited,
        absolute=release.absolute,
        alt_title=_alt_title(name, release),
        folder_title=_folder_title(parent, release),
        marks=release.marks,
        arc=_arc(parts, release),
    )
    log.info(
        "Guessed: %s - E%s %s", " ".join(filter(None, (info.query, info.media_format))), episode, info.year or ""
    )
    return info


if __name__ == "__main__":
    # Try names from a terminal and watch the parser work: python3 -m anilist_updater.parsing "name.mkv" ...
    import sys

    logging.basicConfig(stream=sys.stdout, level=logging.DEBUG, format="%(message)s")
    for argument in sys.argv[1:]:
        try:
            print(f"=> {parse_path(argument)}\n")
        except ParseError as error:
            print(f"=> skipped: {error}\n")
