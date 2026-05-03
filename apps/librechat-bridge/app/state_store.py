"""Persist MongoDB change-stream resume token between restarts."""

import json
from pathlib import Path

PATH = Path("/tmp/bridge_resume_token.json")


def load_resume_token() -> dict | None:
    if not PATH.exists():
        return None
    try:
        return json.loads(PATH.read_text())
    except Exception:
        return None


def save_resume_token(token: dict | None) -> None:
    if token is None:
        return
    try:
        PATH.write_text(json.dumps(token, default=str))
    except Exception:
        # If we can't persist the resume token, the worst case is reprocessing on restart
        # (idempotency at memory-api side handles dup). Don't crash the watcher.
        pass
