"""The loading screen from the orchestrator's side: raised, then taken down.

Raised before the launch's own work -- the taskbar pin, the players' engine, the
session machinery's import -- which he watched a bare desktop through.  What is
imported here is paid for before the cover is up, so there is a test that
nothing else is (``test_the_launch_loads_nothing_the_cover_does_not_need``).
"""
from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .checkout_overrides import genau_project_kwargs
from .child_launch import no_child_log, no_console_window
from .loading_screen import WINDOW_TITLE
from .overlay_progress import (
    CANCEL_FILENAME,
    CANCEL_OPENING_FUN_TIME,
    PROGRESS_FILENAME,
    STARTUP_PHASES,
    NullProgress,
    PhaseProgress,
    ProgressReporter,
)
from .process_identity import NAMER
from .session_handoff import (
    DESKTOP,
    VR,
    HandoffTarget,
    drop_crossing_cover,
    returning_from_a_crossing,
)
from .win32 import wait_for_window_by_title

# A child of the orchestrator's own logger: the cover is up before the session
# has handlers of its own, and the launch's log is where its timing is read back.
logger = logging.getLogger("fun_time.orchestrator.cover")

WINDOW_WAIT_S = 5.0
EXIT_WAIT_S = 3.0


@dataclass(frozen=True)
class LoadingCover:
    """The loading screen, or the absence of one on the path without a curtain."""

    process: subprocess.Popen | None  # None where a run wants no cover at all
    progress: ProgressReporter
    hwnd: int
    progress_file: Path
    cancel_file: Path
    turns_back_to: HandoffTarget | None

    def take_it_down(self) -> None:
        """DONE, then hold until the screen is off the monitors: a cover left
        standing outlives the launch by its whole staleness guard."""
        self.progress.finish()
        if self.process is not None:
            try:
                self.process.wait(timeout=EXIT_WAIT_S)
            except subprocess.TimeoutExpired:
                self.process.kill()
                logger.warning("Loading screen did not exit, killed")
        self.progress_file.unlink(missing_ok=True)


def open_the_cover(state_dir: Path, *, show_overlays: bool, project_dirs: str,
                   cancelable: bool = True) -> LoadingCover:
    """The loading screen over every monitor, its window resolved."""
    returning = returning_from_a_crossing(state_dir)
    esc_cancels = ("" if not cancelable
                   else DESKTOP.crossing_hint if returning
                   else CANCEL_OPENING_FUN_TIME)
    turns_back_to = VR if returning and esc_cancels else None
    progress_file = state_dir / PROGRESS_FILENAME
    cancel_file = state_dir / CANCEL_FILENAME
    # Clear a cancel flag left over from a previous session so it can't abort
    # this one before the user has touched anything.
    if turns_back_to is None:
        cancel_file.unlink(missing_ok=True)
    if not show_overlays:
        return LoadingCover(None, NullProgress(), 0, progress_file, cancel_file, turns_back_to)

    progress = PhaseProgress(progress_file, cancel_file=cancel_file, hint=esc_cancels)
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress.announce(STARTUP_PHASES[0].key)
    loading_proc = subprocess.Popen(
        [
            NAMER.named_exe(sys.executable, "LoadingScreen"),
            "-m", "fun_time.loading_screen", str(progress_file),
        ],
        **no_child_log(),
        **no_console_window(),
        **genau_project_kwargs(project_dirs),
    )
    logger.info("Loading screen launched (pid=%d)", loading_proc.pid)
    overlay_hwnd = wait_for_window_by_title(
        WINDOW_TITLE, timeout_s=WINDOW_WAIT_S, exact=True, include_hidden=True,
    )
    if overlay_hwnd:
        logger.info("Loading cover resolved (hwnd=%d)", overlay_hwnd)
    else:
        logger.warning("The loading cover's window did not appear; startup "
                       "will show through whatever it raises")
    # Handed over here, not at the reveal, which it would sit on top of.
    drop_crossing_cover(state_dir)
    return LoadingCover(
        loading_proc, progress, overlay_hwnd, progress_file, cancel_file, turns_back_to,
    )
