from __future__ import annotations

import contextlib
import os
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from app_support.threading_utils import start_daemon_thread

PROGRESS_FILENAME = "startup_progress.txt"
SHUTDOWN_PROGRESS_FILENAME = "shutdown_progress.txt"

CANCEL_FILENAME = "startup_cancel.flag"
CANCEL_WORD = "cancel"
QUIT_WORD = "quit"
CANCELING = "Canceling..."
CANCEL_OPENING_FUN_TIME = "Press Esc to cancel opening Fun Time"
CANCEL_OPENING_FUN_TIME_VR = "Press Esc to cancel opening Fun Time VR"
CANCEL_ENTERING_VR = "Press Esc to cancel entering VR"
CANCEL_EXITING_VR = "Press Esc to cancel exiting VR"
CANCEL_CLOSING_FUN_TIME = "Press Esc to cancel closing Fun Time"
CANCEL_CLOSING_FUN_TIME_VR = "Press Esc to cancel closing Fun Time VR"

SHUTDOWN_READY_FILENAME = "shutdown_ready.flag"


@dataclass(frozen=True)
class Progress:
    step: int = 0
    total: int = 1
    message: str = ""
    hint: str = ""
    done: bool = False
    malformed: bool = False


def parse_progress(text: str) -> Progress:
    text = text.strip()
    if text == "DONE":
        return Progress(done=True)
    try:
        position, message, hint = (text.split("|", 2) + ["", ""])[:3]
        step_str, total_str = position.split("/")
        return Progress(step=int(step_str), total=int(total_str), message=message, hint=hint)
    except (ValueError, IndexError):
        return Progress(malformed=True)


def cancel_file_for(progress_file: str | Path) -> Path:
    return Path(progress_file).with_name(CANCEL_FILENAME)


def what_the_flag_asks(cancel_file: Path) -> str:
    try:
        words = cancel_file.read_text(encoding="utf-8").split()
    except OSError:
        return ""
    return QUIT_WORD if QUIT_WORD in words else CANCEL_WORD


def ready_file_for(progress_file: str | Path) -> Path:
    return Path(progress_file).with_name(SHUTDOWN_READY_FILENAME)


def progress_file_argument(module: str) -> Path:
    match sys.argv:
        case [_, progress_file, *_]:
            return Path(progress_file)
    print(f"Usage: python -m {module} <progress_file>", file=sys.stderr)
    sys.exit(1)


class StartupCancelled(Exception):
    def __init__(self, launched_pids: list[int] | None = None, rfb_hwnd: int = 0) -> None:
        super().__init__("Startup cancelled by user")
        self.launched_pids: list[int] = launched_pids if launched_pids is not None else []
        self.rfb_hwnd = rfb_hwnd
        self.origenerator_taken_over = False


def loading_screen_is_up(state_dir: Path) -> bool:
    return (Path(state_dir) / PROGRESS_FILENAME).exists()


def startup_still_building(state_dir: Path) -> bool:
    path = Path(state_dir) / PROGRESS_FILENAME
    try:
        progress = parse_progress(path.read_text(encoding="utf-8"))
    except OSError:
        return False
    return not progress.done and not (
        progress.total > 0 and progress.step >= progress.total)


@dataclass(frozen=True)
class Phase:
    key: str
    message: str
    weight: float


LAUNCH_PHASES: tuple[Phase, ...] = (
    Phase("starting", "Starting...", 0.3),
    Phase("engine", "Checking the video engine...", 1.0),
)

ACTIVATING_ORIGENERATOR: Phase = Phase("origenerator", "Activating Origenerator...", 0.5)
STARTING_VOICE_COMMANDS: Phase = Phase("voice", "Starting voice commands...", 0.5)

APP_PHASES: tuple[Phase, ...] = (
    Phase("services", "Preparing services...", 0.7),
    Phase("browser", "Launching browser...", 0.4),
    Phase("companions", "Launching companions...", 1.3),
    Phase("funestras", "Waiting for Funestras...", 0.5),
    Phase("windows", "Positioning windows...", 0.5),
    ACTIVATING_ORIGENERATOR,
    STARTING_VOICE_COMMANDS,
    Phase("finalizing", "Finalizing...", 0.0),
)

STARTUP_PHASES: tuple[Phase, ...] = (*LAUNCH_PHASES, *APP_PHASES)

# The teardown as the closing screen sees it.  NOT seconds: a part ends when
# Windows says it has, so the bar walks the steps.  No weightless phase ends
# this one -- the orchestrator writes DONE once the last child is gone.
SHUTDOWN_PHASES: tuple[Phase, ...] = (
    Phase("controls", "Closing...", 1.0),
    Phase("browser", "Closing browser...", 1.0),
    Phase("funestras", "Closing Funestras...", 1.0),
    Phase("companions", "Closing companions...", 1.0),
)


@runtime_checkable
class ProgressReporter(Protocol):
    def advance(self, phase: str) -> None: ...
    def announce(self, phase: str) -> None: ...
    def finish(self) -> None: ...
    def loading_screen_kept_up(self) -> contextlib.AbstractContextManager[None]: ...
    @property
    def cancelled(self) -> bool: ...


class PhaseProgress:
    def __init__(
        self,
        progress_file: Path,
        *,
        phases: tuple[Phase, ...] = STARTUP_PHASES,
        cancel_file: Path | None = None,
        hint: str = "",
    ) -> None:
        self._progress_file = progress_file
        self._phases = phases
        self._cancel_file = cancel_file
        self._hint = hint

    @property
    def cancelled(self) -> bool:
        if self._cancel_file is None:
            return False
        asked = what_the_flag_asks(self._cancel_file)
        return asked == QUIT_WORD or (asked == CANCEL_WORD and bool(self._hint))

    def advance(self, phase: str) -> None:
        if self.cancelled:
            raise StartupCancelled()
        self.announce(phase)

    def announce(self, phase: str) -> None:
        entered = self._phase_index(phase)
        done = round(sum(p.weight for p in self._phases[:entered]) * 100)
        total = round(sum(p.weight for p in self._phases) * 100)
        line = f"{done}/{total}|{self._phases[entered].message}"
        self._progress_file.write_text(
            f"{line}|{self._hint}" if self._hint else line, encoding="utf-8")

    def _phase_index(self, key: str) -> int:
        for index, phase in enumerate(self._phases):
            if phase.key == key:
                return index
        raise KeyError(f"unknown phase: {key!r}")

    def finish(self) -> None:
        self._progress_file.write_text("DONE", encoding="utf-8")

    @contextlib.contextmanager
    def loading_screen_kept_up(self) -> Iterator[None]:
        stop = keep_the_loading_screen_up(self._progress_file, name="loading-screen-kept-up")
        try:
            yield
        finally:
            stop()


class NullProgress:
    cancelled = False

    def advance(self, phase: str) -> None:
        pass

    def announce(self, phase: str) -> None:
        pass

    def finish(self) -> None:
        pass

    def loading_screen_kept_up(self) -> contextlib.AbstractContextManager[None]:
        return contextlib.nullcontext()


LOADING_SCREEN_HEARTBEAT_S = 1.0


def keep_the_loading_screen_up(progress_file: Path, *, name: str) -> Callable[[], None]:
    stopped = threading.Event()

    def beat() -> None:
        while True:
            try:
                if not parse_progress(progress_file.read_text(encoding="utf-8")).done:
                    os.utime(progress_file, None)
            except OSError:
                pass
            if stopped.wait(LOADING_SCREEN_HEARTBEAT_S):
                return

    heartbeat = start_daemon_thread(target=beat, name=name)

    def stop() -> None:
        stopped.set()
        heartbeat.join()

    return stop
