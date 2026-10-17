"""Exceptions that map onto the response `kind` sent back to mpv."""


class UpdaterError(Exception):
    """Base class for expected failures."""


class SkipUpdate(UpdaterError):
    """Nothing to do or nothing found; not worth interrupting the user unless they asked."""


class ParseError(SkipUpdate):
    """No usable title could be extracted from the path."""


class AniListError(UpdaterError):
    """The AniList API (or the network to it) failed."""

    def __init__(self, message: str, status: int | None = None) -> None:
        """Store the HTTP status alongside the message."""
        super().__init__(message)
        self.status = status
