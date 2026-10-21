"""Process entry point: one JSON request on stdin, one JSON response on stdout, logs on stderr."""

import json
import logging
import sys
from typing import Any

from .api import AniListClient
from .cache import Cache, Pending
from .errors import SkipUpdate
from .models import AnimeInfo, Correction, Options, Outcome
from .service import Service

log = logging.getLogger(__name__)


def _run(request: dict[str, Any]) -> Outcome:
    client = AniListClient.from_token_file()
    if request["action"] == "search":
        return Outcome(results=client.search_titles(request["query"]))
    if request["action"] == "score":
        score_format, value = client.score(int(request["anime_id"]))
        return Outcome(score={"format": score_format, "value": value})

    options = Options.from_dict(request.get("options"))
    service = Service(options, client, Cache(options), Pending())
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


def _failure(kind: str, message: str) -> dict[str, Any]:
    return {"ok": False, "kind": kind, "message": message, "info": None}


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
        return _failure("skip", str(e))
    except Exception as e:
        log.debug("Failed", exc_info=True)
        return _failure("error", str(e) or type(e).__name__)

    return {
        "ok": True,
        "kind": "success",
        "message": outcome.message,
        "info": outcome.info.to_dict() if outcome.info else None,
        "results": outcome.results,
        "score": outcome.score,
    }


def main() -> None:
    """Read the request from stdin and write the response to stdout, both as UTF-8."""
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")

    try:
        request = json.loads(sys.stdin.buffer.read())
    except ValueError:
        response = _failure("error", "Invalid request.")
    else:
        response = handle(request)
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode())
