"""The note saying what asked a session to end: whatever asks writes one before
sending the exit, and the orchestrator reads and removes it.  Without it, an end
nobody asked for reads exactly like one the user asked for
(``tests/test_session_end_reason.py``).
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

SESSION_END_MARKER = "session_end.txt"


def session_end_marker_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / SESSION_END_MARKER


def mark_session_end(state_dir: str | Path, reason: str) -> None:
    path = session_end_marker_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(reason, encoding="utf-8")
    except OSError:  # a session comes down whether or not it can leave a note
        logger.warning("Could not write %s", path, exc_info=True)

