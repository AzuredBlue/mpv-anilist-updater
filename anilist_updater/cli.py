"""Process entry point: one JSON request on stdin, one JSON response on stdout, logs on stderr."""

import json
import logging
import sys
from typing import Any

from .api import AniListClient
from .cache import Cache
from .errors import SkipUpdate
from .models import AnimeInfo, Correction, Options, Outcome
from .service import Service

log = logging.getLogger(__name__)


def _run(request: dict[str, Any]) -> Outcome:
    options = Options.from_dict(request.get("options"))
    service = Service(options, AniListClient.from_token_file(), Cache(options))
    path = request["path"]
    info = AnimeInfo.from_dict(request.get("info"))

    match request["action"]:
        case "info":
            return service.info(path)
        case "update":
            if info is None:
                raise SkipUpdate("Anime info not loaded yet.")
            return service.update(path, info)
        case "correct":
            return service.correct(path, info, Correction.from_dict(request["correction"]))
        case action:
            raise ValueError(f"Unknown action: {action}")


def handle(request: dict[str, Any]) -> dict[str, Any]:
    """Run a request and describe the result.

    Args:
        request (dict[str, Any]): `action`, `path`, `options` and, per action, `info` or `correction`.

    Returns:
        dict[str, Any]: `ok`, `kind` ("success", "skip" or "error"), `message` and the new `info`.
    """
    try:
        outcome = _run(request)
    except SkipUpdate as e:
        log.info("Skipped: %s", e)
        return {"ok": False, "kind": "skip", "message": str(e), "info": None}
    except Exception as e:
        log.exception("Failed")
        return {"ok": False, "kind": "error", "message": str(e) or type(e).__name__, "info": None}

    return {
        "ok": True,
        "kind": "success",
        "message": outcome.message,
        "info": outcome.info.to_dict() if outcome.info else None,
    }


def main() -> None:
    """Read the request from stdin and write the response to stdout, both as UTF-8."""
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")

    try:
        request = json.loads(sys.stdin.buffer.read())
    except ValueError:
        response = {"ok": False, "kind": "error", "message": "Invalid request.", "info": None}
    else:
        response = handle(request)
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode())
