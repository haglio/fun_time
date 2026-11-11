from __future__ import annotations

import logging
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .funestra_deaths import the_part_that_closed

logger = logging.getLogger(__name__)

ASKED = "asked"
A_PART = "part"
THE_HOTKEYS = "ahk"

SESSION_END_MARKER = "session_end.txt"


def session_end_marker_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / SESSION_END_MARKER


def session_end_asked(state_dir: str | Path) -> bool:
    return session_end_marker_path(state_dir).exists()


def mark_session_end(state_dir: str | Path, reason: str) -> None:
    path = session_end_marker_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(reason, encoding="utf-8")
    except OSError:
        logger.warning("Could not write %s", path, exc_info=True)



def describe_session_end(state_dir: Path, exit_code: int) -> str:
    marker = session_end_marker_path(state_dir)
    try:
        reason = marker.read_text(encoding="utf-8").strip()
    except OSError:
        reason = ""
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not clear %s", marker, exc_info=True)
    if reason:
        return f"asked to end: {reason}"
    if exit_code:
        return "the hotkey script FAILED -- see its own MsgBox/exit path"
    return "UNEXPECTED -- nothing asked it to end; the hotkey script exited on its own"


@dataclass(frozen=True)
class SessionEnd:
    by: str
    exit_code: int
    closed: str | None = None


def wait_for_the_session_to_end(
    hotkeys: subprocess.Popen, state_dir: Path, *, parts: Mapping,
    ending_it: Mapping[str, subprocess.Popen] | None = None, poll_s: float = 0.1,
) -> SessionEnd:
    marker = session_end_marker_path(state_dir)
    while hotkeys.poll() is None:
        if marker.exists():
            return SessionEnd(ASKED, 0)
        for name, process in (ending_it or {}).items():
            if process.poll() is not None:
                return SessionEnd(name, 0)
        closed = the_part_that_closed(parts)
        if closed is not None:
            return SessionEnd(A_PART, 1, closed)
        time.sleep(poll_s)
    return SessionEnd(THE_HOTKEYS, hotkeys.wait())
