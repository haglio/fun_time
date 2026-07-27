"""What the overlays read, and what the orchestrator writes for them.

Both ends of a session put a cover over the screen — ``loading_screen`` while
the windows arrive, ``closing_screen`` while they go — and both watch a progress
file in the state dir for how far along the orchestrator has got.  This module
is that channel: the file names, the phases each end walks through, and the
writer on the orchestrator's side.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

PROGRESS_FILENAME = "startup_progress.txt"
SHUTDOWN_PROGRESS_FILENAME = "shutdown_progress.txt"

# The loading screen drops this flag beside the progress file when the user
# presses Esc; the orchestrator's progress reporter watches for it and raises
# StartupCancelled at the next phase boundary so startup unwinds.
CANCEL_FILENAME = "startup_cancel.flag"

# The closing screen drops this flag beside its own progress file once it is
# painted over every monitor.  Teardown waits for it before killing anything:
# a cover that is not up yet hides nothing.
SHUTDOWN_READY_FILENAME = "shutdown_ready.flag"


def cancel_file_for(progress_file: str | Path) -> Path:
    """The cancel flag that pairs with *progress_file* (its sibling in the
    state dir).  Both the loading screen and the orchestrator derive the path
    this way, so they always agree on it."""
    return Path(progress_file).with_name(CANCEL_FILENAME)


def ready_file_for(progress_file: str | Path) -> Path:
    """The ready flag that pairs with a shutdown *progress_file*.  Derived the
    way the cancel flag is, so the closing screen and the orchestrator agree on
    it without passing it around."""
    return Path(progress_file).with_name(SHUTDOWN_READY_FILENAME)


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
class Phase:
    """One reported step of a sequence: what to call it, and how much of the bar
    it spans.  What a weight measures is each sequence's own business — see the
    phase tuples below."""

    key: str
    message: str
    weight: float


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
STARTUP_PHASES: tuple[Phase, ...] = (
    Phase("services", "Preparing services...", 0.7),
    Phase("browser", "Launching browser...", 0.4),
    Phase("companions", "Launching companions...", 1.3),
    Phase("players", "Waiting for players...", 0.5),
    Phase("windows", "Positioning windows...", 0.5),
    Phase("finalizing", "Finalizing...", 0.0),
)

# The teardown as the closing screen sees it.  These weights are NOT seconds:
# a taskkill returns when Windows says so, and the whole sequence is over in a
# couple of seconds, so there is nothing here worth timing and the bar simply
# walks the steps.  No weightless phase ends this one — the orchestrator writes
# DONE once the last child is gone.
SHUTDOWN_PHASES: tuple[Phase, ...] = (
    # The screen opens on this one, so its wording is the screen's own opening
    # status — anything else would read as a flicker on the first poll.
    Phase("controls", "Closing...", 1.0),
    Phase("browser", "Closing browser...", 1.0),
    Phase("players", "Closing players...", 1.0),
    Phase("companions", "Closing companions...", 1.0),
)


@runtime_checkable
class ProgressReporter(Protocol):
    def advance(self, phase: str) -> None: ...
    def finish(self) -> None: ...
    @property
    def cancelled(self) -> bool: ...


class PhaseProgress:
    """Writes progress updates to a file for an overlay to read.

    Each ``advance`` names the phase being entered.  Given a cancel file it is
    also a cancellation checkpoint: if the overlay has dropped that flag, the
    phase is aborted before it runs by raising ``StartupCancelled``.  Only
    startup passes one — a teardown has nothing left to call off.
    """

    def __init__(
        self,
        progress_file: Path,
        *,
        phases: tuple[Phase, ...] = STARTUP_PHASES,
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
        # Hundredths of a unit: the overlay reads two integers.  The position is
        # the work ALREADY behind us, so only a weightless final phase can put it
        # on the total — and the total is what tells the screen to close, so
        # reporting a phase's own weight as it starts would shut the cover early.
        done = round(sum(p.weight for p in self._phases[:entered]) * 100)
        total = round(sum(p.weight for p in self._phases) * 100)
        self._progress_file.write_text(
            f"{done}/{total}|{self._phases[entered].message}",
            encoding="utf-8",
        )

    def _phase_index(self, key: str) -> int:
        for index, phase in enumerate(self._phases):
            if phase.key == key:
                return index
        raise KeyError(f"unknown phase: {key!r}")

    def finish(self) -> None:
        self._progress_file.write_text("DONE", encoding="utf-8")


class NullProgress:
    """Silent no-op progress reporter for integration mode."""

    cancelled = False

    def advance(self, phase: str) -> None:
        pass

    def finish(self) -> None:
        pass
