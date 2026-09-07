"""What the covers read, and what an orchestrator writes for them.

Both ends of a session put a cover up — ``loading_screen`` while the windows
arrive, ``closing_screen`` while they go, and :mod:`fun_time_vr.cover` for both
ends in the headset — and each watches a progress file in the state dir for how
far the orchestrator has got.  This module is that channel: the file names, the
phases, and the writer on the orchestrator's side.

Esc reaches an orchestrator two ways — a cover's own binding, which needs the
focus, and the hotkey script's hook, which does not and so still works once
something else has taken it — so a cover follows the FLAG, not a keypress, and
stays up reading "Cancelling..." until the teardown finishes.
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


@dataclass(frozen=True)
class Progress:
    """One line of the progress file, parsed.  *malformed* separates "nothing
    written yet" from "wrote something we could not read"; both used to arrive
    as step 0 of 1, which reads as a genuine first phase."""

    step: int = 0
    total: int = 1
    message: str = ""
    done: bool = False
    malformed: bool = False


def parse_progress(text: str) -> Progress:
    """One line of the progress file the orchestrator writes."""
    text = text.strip()
    if text == "DONE":
        return Progress(done=True)
    try:
        parts = text.split("|", 1)
        step_str, total_str = parts[0].split("/")
        return Progress(
            step=int(step_str),
            total=int(total_str),
            message=parts[1] if len(parts) > 1 else "",
        )
    except (ValueError, IndexError):
        return Progress(malformed=True)


def cancel_file_for(progress_file: str | Path) -> Path:
    """The cancel flag pairing with *progress_file*, its sibling in the state
    dir.  Every end derives it this way, so none has to be told."""
    return Path(progress_file).with_name(CANCEL_FILENAME)


def ready_file_for(progress_file: str | Path) -> Path:
    """The ready flag pairing with a shutdown *progress_file*, derived the way
    the cancel flag is."""
    return Path(progress_file).with_name(SHUTDOWN_READY_FILENAME)


class StartupCancelled(Exception):
    """Raised out of a progress checkpoint when the user cancels startup.

    Carries what had been launched when it unwound, so the orchestrator can tear
    those children down; the sequencer fills these in as it re-raises.
    """

    def __init__(self, launched_pids: list[int] | None = None, rfb_hwnd: int = 0) -> None:
        super().__init__("Startup cancelled by user")
        self.launched_pids: list[int] = launched_pids if launched_pids is not None else []
        self.rfb_hwnd = rfb_hwnd


def loading_cover_is_up(state_dir: Path) -> bool:
    """Whether the startup cover is still on the screen.

    The file exists for the duration of startup, so its presence answers this.
    Distinct from :func:`startup_still_building`, which goes False one phase
    earlier: a window that must be IN PLACE when the cover lifts asks that one,
    anything seen THROUGH the cover asks this one.
    """
    return (Path(state_dir) / PROGRESS_FILENAME).exists()


def startup_still_building(state_dir: Path) -> bool:
    """True while startup is still assembling the room, so a companion window of
    the session's own must stay out of the cover's way.

    Goes False one phase EARLY, at the final weightless phase with the cover
    still up: a companion that waited for the lift would arrive late on a room
    that was supposed to be finished.  A missing file answers False.
    """
    path = Path(state_dir) / PROGRESS_FILENAME
    try:
        progress = parse_progress(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    return not progress.done and not (
        progress.total > 0 and progress.step >= progress.total)


@dataclass(frozen=True)
class Phase:
    """One reported step: what to call it, and how much of the bar it spans.
    What a weight measures is each sequence's own business."""

    key: str
    message: str
    weight: float


# The startup sequence as the loading screen sees it, in order.  The bar
# advances by TIME rather than step count: an equal share per step parked it at
# 83% through the one phase that waits on other processes.  The last is
# weightless so the bar reads full while the room is settled under the cover.
STARTUP_PHASES: tuple[Phase, ...] = (
    Phase("services", "Preparing services...", 0.7),
    Phase("browser", "Launching browser...", 0.4),
    Phase("companions", "Launching companions...", 1.3),
    Phase("players", "Waiting for players...", 0.5),
    # The long one: the hosted app's window lands 10-28s after launch (its own
    # boot log) against 5-8s for the rest of the room.
    Phase("origenerator", "Waiting for Origenerator...", 9.0),
    Phase("windows", "Positioning windows...", 0.5),
    Phase("finalizing", "Finalizing...", 0.0),
)

# The teardown as the closing screen sees it.  NOT seconds: a taskkill returns
# when Windows says so, so the bar walks the steps.  No weightless phase ends
# this one -- the orchestrator writes DONE once the last child is gone.
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
    """Writes progress updates to a file for a cover to read.  Each ``advance``
    names the phase entered, and with a cancel file is a checkpoint too: a
    dropped flag aborts the phase before it runs.  Startup alone passes one."""

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
        # Hundredths of a unit: the cover reads two integers.  The position is
        # work ALREADY done, so only a weightless final phase reaches the total.
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
