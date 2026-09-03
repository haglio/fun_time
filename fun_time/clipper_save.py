"""Saving a Clipper session — the one place the dispatcher's world shells out
to a sibling repo, running clipper's own venv for the video Nau is showing."""
from __future__ import annotations

import functools
import logging
import subprocess
import sys
from pathlib import Path

from .bridge_records import BridgeConfig
from .player_status import read_nau_status

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _clipper_project_dir() -> Path:
    """The sibling clipper checkout, beside the PRIMARY checkout — never beside
    a worktree, where ``../clipper`` does not exist (the two tests on this pin
    both halves).  Cached: one git subprocess, and the answer cannot change
    while the session runs.
    """
    from .branch_session import primary_checkout  # avoids a launcher import on the hot path

    try:
        return primary_checkout().parent / "clipper"
    except (OSError, subprocess.SubprocessError):
        return Path(__file__).resolve().parents[1].parent / "clipper"


def _clipper_python() -> str:
    clipper_python = _clipper_project_dir() / ".venv" / "Scripts" / "python.exe"
    if clipper_python.is_file():
        return str(clipper_python)
    return sys.executable


def _current_main_media(config: BridgeConfig) -> tuple[str, float]:
    """The main player's current video path and playback time (seconds).

    Nau owns the main player in every mode it appears (nau and hybrid) and
    publishes both in its status file; the path is empty when nothing is playing.
    """
    status = read_nau_status(config.nau_status_file)
    return status.video, status.position_ms / 1000


def save_clip_session(config: BridgeConfig) -> str:
    """Save a Clipper session for the main player's current video.

    Returns a short user-visible message on success, or empty string on failure.
    """
    video_path, playback_time = _current_main_media(config)
    if not video_path:
        logger.warning("clipper_save: no video playing on the main player")
        return ""
    try:
        result = subprocess.run(
            [
                _clipper_python(), "-m", "clipper.create_session",
                "--video", video_path,
                "--time", str(playback_time),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(_clipper_project_dir()),
        )
        if result.returncode == 0:
            session_path = result.stdout.strip()
            logger.info("clipper_save: %s", session_path)
            name = Path(session_path).stem if session_path else "session"
            return f"Clipper: {name}"
        logger.warning("clipper_save failed: %s", result.stderr.strip())
        return ""
    except (OSError, subprocess.SubprocessError) as exc:
        # Only the OS and the subprocess machinery fail on clipper's behalf
        # (TimeoutExpired included); a TypeError in our own argument building
        # surfaces instead of reading as "clipper failed".
        logger.warning("clipper_save error: %s", exc)
        return ""
