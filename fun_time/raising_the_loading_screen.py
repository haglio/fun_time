from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from app_support import loading_screen as shared_loading_screen
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
    drop_transition_screen,
    returning_from_a_crossing,
)
from .win32 import find_window_for_process, wait_for_window

logger = logging.getLogger("fun_time.orchestrator.loading_screen")

WINDOW_WAIT_S = 5.0
WINDOW_WAIT_UNDER_A_CROSSING_S = 20.0
OFF_THE_MONITORS_WAIT_S = 3.0


@dataclass(frozen=True)
class RaisedLoadingScreen:
    process: subprocess.Popen | None
    progress: ProgressReporter
    progress_file: Path
    cancel_file: Path
    turns_back_to: HandoffTarget | None

    def visible_window(self) -> int:
        return find_window_for_process(self.process.pid, WINDOW_TITLE)

    def take_it_down(self) -> None:
        self.progress.finish()
        if self.process is not None:
            try:
                self.process.wait(timeout=OFF_THE_MONITORS_WAIT_S)
            except subprocess.TimeoutExpired:
                self.process.kill()
                logger.warning("Loading screen did not exit, killed")
        self.progress_file.unlink(missing_ok=True)


def open_the_loading_screen(state_dir: Path, *, show_overlays: bool, sibling_checkouts: str,
                   cancelable: bool = True) -> RaisedLoadingScreen:
    returning = returning_from_a_crossing(state_dir)
    esc_cancels = ("" if not cancelable
                   else DESKTOP.crossing_hint if returning
                   else CANCEL_OPENING_FUN_TIME)
    turns_back_to = VR if returning and esc_cancels else None
    progress_file = state_dir / PROGRESS_FILENAME
    cancel_file = state_dir / CANCEL_FILENAME
    keeps_an_esc_pressed_during_the_changeover = turns_back_to is not None
    if not keeps_an_esc_pressed_during_the_changeover:
        cancel_file.unlink(missing_ok=True)
    if not show_overlays:
        return RaisedLoadingScreen(None, NullProgress(), progress_file, cancel_file, turns_back_to)

    progress = PhaseProgress(progress_file, cancel_file=cancel_file, hint=esc_cancels)
    progress_file.parent.mkdir(parents=True, exist_ok=True)
    progress.announce(STARTUP_PHASES[0].key)
    handed = shared_loading_screen.handed_over(os.environ)
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
    loading_screen = RaisedLoadingScreen(loading_proc, progress, progress_file, cancel_file, turns_back_to)
    wait_s = WINDOW_WAIT_UNDER_A_CROSSING_S if returning else WINDOW_WAIT_S
    if overlay_hwnd := wait_for_window(loading_screen.visible_window, wait_s):
        logger.info("Loading screen resolved (hwnd=%d)", overlay_hwnd)
    else:
        logger.warning("The loading screen's window was not up after %.0fs; the launch "
                       "goes on while it comes up", wait_s)
    drop_transition_screen(state_dir)
    return loading_screen
