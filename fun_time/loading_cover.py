"""The loading screen from the orchestrator's side: raised, then taken down.

Raised before the launch's own work -- the taskbar pin, the Funestras' engine, the
session machinery's import -- which he watched a bare desktop through.  What is
imported here is paid for before the cover is up, so there is a test that
nothing else is (``test_the_launch_loads_nothing_the_cover_does_not_need``).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app_support import loading_screen
from app_support.subprocess_utils import tie_to_this_process

from .checkout_overrides import sibling_checkouts_kwargs
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
from .win32 import find_window_for_process, wait_for_window

# A child of the orchestrator's own logger: the cover is up before the session
# has handlers of its own, and the launch's log is where its timing is read back.
logger = logging.getLogger("fun_time.orchestrator.cover")

WINDOW_WAIT_S = 5.0
WINDOW_WAIT_UNDER_A_CROSSING_S = 20.0
EXIT_WAIT_S = 3.0


@dataclass(frozen=True)
class LoadingCover:
    """The loading screen, or the absence of one on the path without a curtain."""

    process: subprocess.Popen | None  # None where a run wants no cover at all
    progress: ProgressReporter
    progress_file: Path
    cancel_file: Path
    turns_back_to: HandoffTarget | None

    def window(self) -> int:
        return find_window_for_process(self.process.pid, WINDOW_TITLE)

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


def open_the_cover(state_dir: Path, *, show_overlays: bool, sibling_checkouts: str,
                   cancelable: bool = True) -> LoadingCover:
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
        return LoadingCover(None, NullProgress(), progress_file, cancel_file, turns_back_to)

    progress = PhaseProgress(progress_file, cancel_file=cancel_file, hint=esc_cancels)
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress.announce(STARTUP_PHASES[0].key)
    handed = loading_screen.handed_over(os.environ)
    environment = sibling_checkouts_kwargs(sibling_checkouts).get("env", dict(os.environ))
    loading_proc = subprocess.Popen(
        [
            NAMER.named_exe(sys.executable, "LoadingScreen"),
            "-m", "fun_time.loading_screen", str(progress_file),
        ],
        **no_child_log(),
        **no_console_window(),
        env={**environment, **handed},
    )
    tie_to_this_process(loading_proc)
    logger.info("Loading screen launched (pid=%d)", loading_proc.pid)
    cover = LoadingCover(loading_proc, progress, progress_file, cancel_file, turns_back_to)
    wait_s = WINDOW_WAIT_UNDER_A_CROSSING_S if returning else WINDOW_WAIT_S
    if overlay_hwnd := wait_for_window(cover.window, wait_s):
        logger.info("Loading cover resolved (hwnd=%d)", overlay_hwnd)
    else:
        logger.warning("The loading cover's window was not up after %.0fs; the launch "
                       "goes on while it comes up", wait_s)
    # Handed over here, not at the reveal, which it would sit on top of.
    drop_crossing_cover(state_dir)
    return cover
