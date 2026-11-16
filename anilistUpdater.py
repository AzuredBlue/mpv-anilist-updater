"""Entry point run by main.lua; see anilist_updater/cli.py."""

import sys
from pathlib import Path

if sys.version_info < (3, 11):  # ruff: ignore[outdated-version-block]
    sys.exit(f"Python 3.11 or newer is required, found {sys.version_info.major}.{sys.version_info.minor}.")

# Embeddable Pythons (a ._pth file) and PYTHONSAFEPATH leave the script's folder off sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from anilist_updater.cli import main

if __name__ == "__main__":
    main()
