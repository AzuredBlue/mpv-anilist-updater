"""Entry point run by main.lua; see anilist_updater/cli.py."""

import sys

if sys.version_info < (3, 11):  # ruff: ignore[outdated-version-block]
    sys.exit(f"Python 3.11 or newer is required, found {sys.version_info.major}.{sys.version_info.minor}.")

from anilist_updater.cli import main

if __name__ == "__main__":
    main()
