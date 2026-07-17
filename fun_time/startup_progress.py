"""Progress reporting for the startup sequence.

Used to communicate startup progress to the loading screen subprocess
via a shared progress file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

PROGRESS_FILENAME = "startup_progress.txt"

# The loading screen drops this flag beside the progress file when the user
# presses Esc; the orchestrator's progress reporter watches for it and raises
# StartupCancelled at the next phase boundary so startup unwinds.
CANCEL_FILENAME = "startup_cancel.flag"


def cancel_file_for(progress_file: str | Path) -> Path:
    """The cancel flag that pairs with *progress_file* (its sibling in the
    state dir).  Both the loading screen and the orchestrator derive the path
    this way, so they always agree on it."""
    return Path(progress_file).with_name(CANCEL_FILENAME)


class StartupCancelled(Exception):
    """Raised out of a progress checkpoint when the user cancels startup.

    Carries what the sequencer had launched by the time it unwound, so the
    orchestrator can tear those children down.  The sequencer fills these in as
    it re-raises; a checkpoint raises it bare.
    """

    def __init__(self, launched_pids: list[int] | None = None, rfb_hwnd: int = 0) -> None:
        super().__init__("Startup cancelled by user")
        self.launched_pids: list[int] = launched_pids if launched_pids is not None else []
        self.rfb_hwnd = rfb_hwnd


def loading_screen_active(state_dir: Path) -> bool:
    """True while the startup loading overlay is up.

    The overlay writes ``startup_progress.txt`` in the state dir for the
    duration of startup and deletes it when it closes, so its presence is the
    cue for other always-on-top windows to stay out of its way.
    """
    return (Path(state_dir) / PROGRESS_FILENAME).exists()


@runtime_checkable
class ProgressReporter(Protocol):
    def advance(self, message: str) -> None: ...
    def finish(self) -> None: ...
    @property
    def cancelled(self) -> bool: ...


class StartupProgress:
    """Writes progress updates to a file for the loading screen to read.

    Each ``advance`` is also a cancellation checkpoint: if the loading screen
    has dropped the cancel flag, the step is aborted before it runs by raising
    ``StartupCancelled``.
    """

    def __init__(
        self,
        progress_file: Path,
        total_steps: int,
        cancel_file: Path | None = None,
    ) -> None:
        self._progress_file = progress_file
        self._total_steps = total_steps
        self._current_step = 0
        self._cancel_file = cancel_file

    @property
    def cancelled(self) -> bool:
        return self._cancel_file is not None and self._cancel_file.exists()

    def advance(self, message: str) -> None:
        if self.cancelled:
            raise StartupCancelled()
        self._current_step += 1
        self._progress_file.write_text(
            f"{self._current_step}/{self._total_steps}|{message}",
            encoding="utf-8",
        )

    def finish(self) -> None:
        self._progress_file.write_text("DONE", encoding="utf-8")


class NullProgress:
    """Silent no-op progress reporter for integration mode."""

    cancelled = False

    def advance(self, message: str) -> None:
        pass

    def finish(self) -> None:
        pass
