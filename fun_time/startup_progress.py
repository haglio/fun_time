"""Progress reporting for the startup sequence.

Used to communicate startup progress to the loading screen subprocess
via a shared progress file.
"""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class StartupPhase:
    """One reported step of startup: what to call it, and how long it takes."""

    key: str
    message: str
    seconds: float


# The startup sequence as the loading screen sees it, in order.  Each phase
# carries its typical duration, and the bar advances by TIME rather than by step
# count: an equal share per step parked it at 83% through the one phase that
# waits on other processes while four sub-second phases split the rest.
#
# The durations are read off state/event_log.jsonl (its entries bracket the first
# three phases) and off a timed satellite launch (0.47s to its window).  They set
# the SHAPE of the bar, so being a few tenths stale costs a little smoothness and
# nothing else.  The last phase closes the overlay and so must be weightless: the
# screen shuts when the reported position reaches the total.
STARTUP_PHASES: tuple[StartupPhase, ...] = (
    StartupPhase("services", "Preparing services...", 0.7),
    StartupPhase("browser", "Launching browser...", 0.4),
    StartupPhase("companions", "Launching companions...", 1.3),
    StartupPhase("players", "Waiting for players...", 0.5),
    StartupPhase("windows", "Positioning windows...", 0.5),
    StartupPhase("finalizing", "Finalizing...", 0.0),
)


@runtime_checkable
class ProgressReporter(Protocol):
    def advance(self, phase: str) -> None: ...
    def finish(self) -> None: ...
    @property
    def cancelled(self) -> bool: ...


class StartupProgress:
    """Writes progress updates to a file for the loading screen to read.

    Each ``advance`` names the phase being entered and is also a cancellation
    checkpoint: if the loading screen has dropped the cancel flag, the phase is
    aborted before it runs by raising ``StartupCancelled``.
    """

    def __init__(
        self,
        progress_file: Path,
        *,
        phases: tuple[StartupPhase, ...] = STARTUP_PHASES,
        cancel_file: Path | None = None,
    ) -> None:
        self._progress_file = progress_file
        self._phases = phases
        self._cancel_file = cancel_file

    @property
    def cancelled(self) -> bool:
        return self._cancel_file is not None and self._cancel_file.exists()

    def advance(self, phase: str) -> None:
        if self.cancelled:
            raise StartupCancelled()
        entered = self._phase_index(phase)
        # Hundredths of a second: the loading screen reads two integers.  The
        # position is the wait ALREADY behind us, so only the final phase can put
        # it on the total — and the total is what tells the screen to close, so
        # reporting a phase's own time as it starts would shut the overlay early.
        done = round(sum(p.seconds for p in self._phases[:entered]) * 100)
        total = round(sum(p.seconds for p in self._phases) * 100)
        self._progress_file.write_text(
            f"{done}/{total}|{self._phases[entered].message}",
            encoding="utf-8",
        )

    def _phase_index(self, key: str) -> int:
        for index, phase in enumerate(self._phases):
            if phase.key == key:
                return index
        raise KeyError(f"unknown startup phase: {key!r}")

    def finish(self) -> None:
        self._progress_file.write_text("DONE", encoding="utf-8")


class NullProgress:
    """Silent no-op progress reporter for integration mode."""

    cancelled = False

    def advance(self, phase: str) -> None:
        pass

    def finish(self) -> None:
        pass
