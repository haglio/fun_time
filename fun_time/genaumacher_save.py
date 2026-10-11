"""Saving a Genaumacher session — the one place the dispatcher's world shells out
to a sibling repo, running genaumacher's own venv for the video the Main Funestra is showing."""
from __future__ import annotations

import functools
import logging
import subprocess
import sys
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs

from .bridge_records import BridgeConfig
from .funestra_status import read_main_funestra_status

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def _genaumacher_project_dir() -> Path:
    """The sibling genaumacher checkout, beside the PRIMARY checkout — never beside
    a worktree, where ``../genaumacher`` does not exist (the two tests on this pin
    both halves).  Cached: one git subprocess, and the answer cannot change
    while the session runs.
    """
    from .branch_session import primary_checkout  # noqa: PLC0415 (no launcher import here)

    try:
        beside = primary_checkout().parent
    except (OSError, subprocess.SubprocessError):
        beside = Path(__file__).resolve().parents[1].parent
    return beside / "genaumacher"


def _genaumacher_python() -> str:
    genaumacher_python = _genaumacher_project_dir() / ".venv" / "Scripts" / "python.exe"
    if genaumacher_python.is_file():
        return str(genaumacher_python)
    return sys.executable


def _current_main_media(config: BridgeConfig) -> tuple[str, float]:
    """The Main Funestra's current video path and playback time (seconds).

    The Main Funestra owns the main slot in kino mode and
    publishes both in its status file; the path is empty when nothing is playing.
    """
    status = read_main_funestra_status(config.main_funestra_status_file)
    return status.video, status.position_ms / 1000


def save_clip_session(config: BridgeConfig) -> str:
    """Save a Genaumacher session for the Main Funestra's current video.

    Returns a short user-visible message on success, or empty string on failure.
    """
    video_path, playback_time = _current_main_media(config)
    if not video_path:
        logger.warning("genaumacher_save: no video playing on the Main Funestra")
        return ""
    try:
        result = subprocess.run(
            [
                _genaumacher_python(), "-m", "genaumacher.create_session",
                "--video", video_path,
                "--time", str(playback_time),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(_genaumacher_project_dir()),
            **hidden_subprocess_kwargs(),
        )
        if result.returncode == 0:
            session_path = result.stdout.strip()
            logger.info("genaumacher_save: %s", session_path)
            name = Path(session_path).stem if session_path else "session"
            return f"Genaumacher: {name}"
        logger.error("genaumacher_save failed: %s", result.stderr.strip())
        return ""
    except (OSError, subprocess.SubprocessError) as exc:
        # Only the OS and the subprocess machinery fail on genaumacher's behalf
        # (TimeoutExpired included); a TypeError in our own argument building
        # surfaces instead of reading as "genaumacher failed".
        logger.error("genaumacher_save error: %s", exc)
        return ""
