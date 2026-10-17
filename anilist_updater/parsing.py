"""Turn a media path into a title, episode, year and format: the filename first, then its folders for a title."""

import logging
from dataclasses import dataclass

from .errors import ParseError
from .models import FileInfo
from .release import Release, parse_release

log = logging.getLogger(__name__)


@dataclass(slots=True)
class _Details:
    """Season, part and year, filled from the filename first and the folders after."""

    season: int | None = None
    part: int | None = None
    year: int | None = None

    def fill_from(self, release: Release) -> None:
        if self.season is None:
            self.season = release.season
        self.part = self.part or release.part
        self.year = self.year or release.year


def _title_from_folders(parts: list[str], details: _Details) -> str:
    """Look for the title in the parent folder, then the grandparent."""
    for folder in reversed(parts[:-1][-2:]):
        release = parse_release(folder, folder=True)
        log.info("Folder guess: %s -> %s", folder, release)

        details.fill_from(release)
        if release.title:
            return release.title
    return ""


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
    if release.skip:
        raise ParseError(f"'{name}' is {release.skip}. Not updating.")

    details = _Details(release.season, release.part, release.year)
    # An episode or season first ("E12 - Welcome") means the filename has no title of its own
    title = release.title or _title_from_folders(parts, details)
    if not title and release.episode_title:
        log.info("No folder has a title; using the text after the episode: %s", release.episode_title)
        title = release.episode_title
    if not title:
        raise ParseError(f"Couldn't find title in filename '{name}'! Guess result: {release}")

    episode = 1 if release.episode is None else release.episode
    info = FileInfo(_compose_title(title, details), episode, details.year, release.media_format)
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
