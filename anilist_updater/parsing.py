"""Turn a media path into a title, episode, year and format: the filename first, then its folders for a title."""

import logging
from dataclasses import dataclass

from .errors import ParseError
from .models import FileInfo
from .release import Release, fold_title, parse_release

log = logging.getLogger(__name__)


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


def _title_from_folders(parts: list[str], details: _Details) -> str:
    """Look for the title in the parent folder, then the grandparent."""
    for folder in reversed(parts[:-1][-2:]):
        release = parse_release(folder, folder=True)
        log.info("Folder guess: %s -> %s", folder, release)

        details.fill_from(release)
        if release.title:
            return release.title
    return ""


def _skip_reason(release: Release, parent: Release | None) -> str | None:
    """Why the file isn't an episode, from its name or from a parent folder like "Extras" or "Season 00"."""
    if release.skip or parent is None:
        return release.skip
    if parent.skip:
        return parent.skip
    if parent.season == 0 and not parent.title and not (release.media_format or parent.media_format):
        return "a season 0 special"
    return None


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


def _compose_title(title: str, details: _Details) -> str:
    """Append what AniList titles carry: season and part."""
    words = [title]
    if details.season is not None and (details.season > 1 or details.part):
        words.append(f"Season {details.season}")
    if details.part:
        words.append(f"Part {details.part}")
    return " ".join(words)


def parse_path(path: str) -> FileInfo:
    """Extract title, episode, year and format from a media path.

    Args:
        path (str): Path to the video, or the media title for streams.

    Returns:
        FileInfo: What the path says about the media.

    Raises:
        ParseError: If the file isn't an episode to record, or neither the filename nor its folders have a title.
    """
    parts = path.replace("\\", "/").split("/")
    name = parts[-1]
    release = parse_release(name)
    log.info("File name guess: %s -> %s", name, release)
    parent = parse_release(parts[-2], folder=True) if len(parts) > 1 else None
    if skip := _skip_reason(release, parent):
        raise ParseError(f"'{name}' is {skip}. Not updating.")

    details = _Details(release.season, release.part, release.year, release.media_format)
    if parent and not parent.title:  # "OVA/", "Specials/"
        details.media_format = details.media_format or parent.media_format
    if folder := _season_folder(parts, parent, release):
        details.fill_from(folder)
    # An episode or season first ("E12 - Welcome") means the filename has no title of its own
    title = release.title or _title_from_folders(parts, details)
    if not title and release.episode_title:
        log.info("No folder has a title; using the text after the episode: %s", release.episode_title)
        title = release.episode_title
    if not title:
        raise ParseError(f"Couldn't find title in filename '{name}'! Guess result: {release}")

    episode = 1 if release.episode is None else release.episode
    composed = _compose_title(title, details)
    # A season taken from a folder may not fit the episode ("Show S3/Show - 38" is absolute), so keep the plain title
    inherited = (details.season, details.part) != (release.season, release.part) and composed != title
    info = FileInfo(composed, episode, details.year, details.media_format, title if inherited else None)
    log.info(
        "Guessed: %s - E%s %s", " ".join(filter(None, (info.title, info.media_format))), episode, info.year or ""
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
