from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Protocol

from app_support import osr2_hold
from app_support.osr2_turn_off_reminder import remind_to_turn_off
from app_support.threading_utils import wait_until
from funestra.file_channel import append_command
from funestra.main_hud import HOLD_VERB

from .broker_control import write_broker_command
from .fun_time_funestras import FunTimeFunestra
from .manifest import CommandFiles, LaunchManifest
from .overlay_progress import (
    CANCEL_FILENAME,
    CANCEL_WORD,
    Phase,
    PhaseProgress,
    ProgressReporter,
    ready_file_for,
    what_the_flag_asks,
)
from .runtime_flow import write_flag_file
from .session_end import force_quit_asked
from .session_handoff import (
    HandoffTarget,
    crossing_progress_path,
    launch_the_way_back_screen,
    pending_handoff,
    request_handoff,
)
from .shared_state import BridgeState, read_shared_state, shared_state_path
from .voice_control import VoiceController
from .windows_bridge_dispatch_loop import DispatchLoopRunner

logger = logging.getLogger(__name__)

DISPATCH_LOOP_STOP_S = 1.0
VOICE_STOP_S = 2.0

A_FRESH_TKINTER_PROCESS_IS_UP_WITHIN_S = 1.0
SLACK_FOR_A_LOADED_MACHINE_S = 4.0
CLOSING_SCREEN_READY_CEILING_S = (
    A_FRESH_TKINTER_PROCESS_IS_UP_WITHIN_S + SLACK_FOR_A_LOADED_MACHINE_S)
CLOSING_SCREEN_POLL_S = 0.05


@dataclass(frozen=True)
class TheControls:
    dispatch_runner: DispatchLoopRunner
    dispatch_thread: threading.Thread
    voice_controller: VoiceController | None = None
    voice_thread: threading.Thread | None = None
    loopback_server: ThreadingHTTPServer | None = None

    def stop_dispatching(self) -> None:
        self.dispatch_runner.stop()
        self.dispatch_thread.join(timeout=DISPATCH_LOOP_STOP_S)
        if self.dispatch_thread.is_alive():
            logger.warning("The dispatch loop was still busy %.1fs after the session ended",
                           DISPATCH_LOOP_STOP_S)

    def stop(self) -> None:
        if self.voice_controller is not None:
            self.voice_controller.stop()
        if self.voice_thread is not None:
            self.voice_thread.join(timeout=VOICE_STOP_S)
        self.dispatch_runner.close()
        if self.loopback_server is not None:
            self.loopback_server.shutdown()
            self.loopback_server.server_close()


class ItsOwnEnding(Protocol):
    def closing_screen(
        self, crossing: HandoffTarget | None, hint: str,
    ) -> AbstractContextManager[ProgressReporter]: ...

    def end_the_children(
        self, shutdown: ProgressReporter, crossing: HandoffTarget | None, hint: str,
    ) -> None: ...

    def stop_the_keys(self) -> None: ...

    def last_of_all(self, crossing: HandoffTarget | None, hint: str) -> None: ...

    def origenerator_stays_open(self) -> bool: ...


@dataclass(frozen=True)
class SessionEnding:
    state_dir: Path
    manifest: LaunchManifest
    controls: TheControls
    quit_hint: str
    esc_over_a_crossing: bool
    closed_app: str = "Fun Time"

    def what_esc_cancels(self, crossing: HandoffTarget | None, *, asked: bool) -> str:
        if crossing is not None:
            return crossing.crossing_hint if self.esc_over_a_crossing else ""
        return self.quit_hint if asked else ""

    def ask_for_the_turn_off_reminder(self) -> None:
        remind_to_turn_off(Path(self.manifest.commands.broker_state_dir), closed=self.closed_app)


def silence_the_funestras(manifest: LaunchManifest) -> None:
    commands = manifest.commands
    for paused_file in (
        commands.main_funestra_paused_file, commands.audio_paused_file,
        commands.origenerator_paused_file,
    ):
        if paused_file.strip():
            write_flag_file(paused_file, True)
    for funestra in FunTimeFunestra.SATELLITES:
        write_flag_file(manifest.satellite(funestra).paused_file, True)


def hold_the_osr2(commands: CommandFiles) -> None:
    ending = read_shared_state(shared_state_path(Path(commands.state_dir))) or BridgeState()
    append_command(Path(commands.genau_cmd_file), "PAUSE")
    write_broker_command(commands.broker_cmd_file, HOLD_VERB.get(ending.osr2_control, osr2_hold.PARK))


def stop_everything(manifest: LaunchManifest, controls: TheControls) -> None:
    controls.stop_dispatching()
    silence_the_funestras(manifest)
    hold_the_osr2(manifest.commands)


def end_the_session(ending: SessionEnding, own: ItsOwnEnding, *, asked: bool) -> None:
    stop_everything(ending.manifest, ending.controls)
    crossing = pending_handoff(ending.state_dir)
    hint = ending.what_esc_cancels(crossing, asked=asked)
    with own.closing_screen(crossing, hint) as shutdown:
        ending.controls.stop()
        own.end_the_children(shutdown, crossing, hint)
        if crossing is None:
            own.stop_the_keys()
        own.last_of_all(crossing, hint)
    if (crossing is None and not force_quit_asked(ending.state_dir)
            and not esc_called_the_quit_off(ending.state_dir, crossing, hint)
            and not own.origenerator_stays_open()):
        ending.ask_for_the_turn_off_reminder()


def esc_called_the_quit_off(state_dir: Path, crossing: HandoffTarget | None, hint: str) -> bool:
    return (crossing is None and hint != ""
            and what_the_flag_asks(state_dir / CANCEL_FILENAME) == CANCEL_WORD)


def wait_for_the_closing_screen(
    ready_file: Path, still_up: Callable[[], bool], *,
    now: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> bool:
    try:
        wait_until(lambda: ready_file.exists() or not still_up(),
                   timeout=CLOSING_SCREEN_READY_CEILING_S, interval=CLOSING_SCREEN_POLL_S,
                   now=now, sleep=sleep)
    except TimeoutError:
        logger.warning("Closing screen not ready after %.1fs; closing anyway",
                       CLOSING_SCREEN_READY_CEILING_S)
        return False
    if not ready_file.exists():
        logger.warning("Closing screen exited before it was ready")
        return False
    return True


def turn_back(
    state_dir: Path, home: HandoffTarget, *, sibling_checkouts: str, wait_for_it: bool,
) -> None:
    logger.info("Esc called the quit off; opening %s again", home.app_name)
    way_back = launch_the_way_back_screen(state_dir, sibling_checkouts=sibling_checkouts)
    if wait_for_it:
        wait_for_the_closing_screen(ready_file_for(crossing_progress_path(state_dir)),
                                    lambda: way_back.poll() is None)
    request_handoff(state_dir, home, cancelable=False)


class Painter(Protocol):
    def raise_it(self) -> None: ...

    def still_up(self) -> bool: ...

    def take_it_down(self, progress: PhaseProgress) -> None: ...


@contextlib.contextmanager
def closing_screen(
    progress_file: Path, *, phases: tuple[Phase, ...], hint: str, painter: Painter,
    now: Callable[[], float] = time.monotonic,
) -> Iterator[ProgressReporter]:
    ready_file = ready_file_for(progress_file)
    ready_file.unlink(missing_ok=True)
    progress = PhaseProgress(progress_file, phases=phases, hint=hint)
    progress.advance("controls")
    painter.raise_it()
    wait_for_the_closing_screen(ready_file, painter.still_up, now=now)
    try:
        yield progress
    finally:
        painter.take_it_down(progress)
        progress_file.unlink(missing_ok=True)
        ready_file.unlink(missing_ok=True)
