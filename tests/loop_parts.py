from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import Path

from fun_time.command_answering import CommandAnswering
from fun_time.dispatch_loop_setup import SessionFiles, SessionStart
from fun_time.origenerator_opening import OrigeneratorOpening
from fun_time.rfb_modes import RfbMode
from fun_time.session_environment import ORDINARY_SESSION, SessionEnvironment
from fun_time.shared_state import BridgeState
from fun_time.window_work import WindowWork
from tests.bridge_configs import made_up_bridge_config


def made_up_session_files(tmp_path: Path, *, manifest: Path | None = None) -> SessionFiles:
    return SessionFiles(
        dashboard_cmd=tmp_path / "dashboard_cmd.txt",
        shared_state=tmp_path / "shared_state.ini",
        ahk_cmd=tmp_path / "ahk_cmd.txt",
        manifest=manifest)


def _done() -> threading.Event:
    done = threading.Event()
    done.set()
    return done


class WindowsThatWriteDownWhatTheyAreAsked:
    def __init__(self, rfb_hwnd: int = 0) -> None:
        self.rfb_hwnd = rfb_hwnd
        self.asked: list[tuple] = []

    def in_turn(self, _what: str, work) -> threading.Event:
        work()
        return _done()

    def moving(self) -> bool:
        return False

    def show(self, window_name: str) -> threading.Event:
        self.asked.append(("show", window_name))
        return _done()

    def hide_after_settle(self, window_name: str) -> threading.Event:
        self.asked.append(("hide_after_settle", window_name))
        return _done()

    def park(self, window_name: str) -> threading.Event:
        self.asked.append(("park", window_name))
        return _done()

    def restore_parked(self) -> threading.Event:
        self.asked.append(("restore_parked",))
        return _done()

    def activate(self, window_name: str) -> threading.Event:
        self.asked.append(("activate", window_name))
        return _done()

    def restack_rfb_mode(self, rfb_mode: RfbMode, *, paused: bool = False) -> threading.Event:
        self.asked.append(("restack_rfb_mode", rfb_mode, paused))
        return _done()

    def remove_all_topmost(self) -> threading.Event:
        self.asked.append(("remove_all_topmost",))
        return _done()

    def restore_all_topmost(self, rfb_mode: RfbMode) -> threading.Event:
        self.asked.append(("restore_all_topmost", rfb_mode))
        return _done()

    def converge_origenerator_window(self, rfb_mode: RfbMode) -> None:
        self.asked.append(("converge_origenerator_window", rfb_mode))

    def minimize_all(self, window_names: Iterable[str]) -> threading.Event:
        self.asked.append(("minimize_all", tuple(window_names)))
        return _done()

    def restore_minimized(self) -> threading.Event:
        self.asked.append(("restore_minimized",))
        return _done()

    def topmost_report(self) -> str:
        return "a made-up report"


def made_up_window_work(tmp_path: Path, *, windows=None,
                        env: SessionEnvironment = ORDINARY_SESSION, **start) -> WindowWork:
    return WindowWork(
        made_up_bridge_config(tmp_path), made_up_session_files(tmp_path),
        SessionStart(windows=windows or WindowsThatWriteDownWhatTheyAreAsked(), **start), env)


def made_up_answering(tmp_path: Path, *, window_work: WindowWork | None = None,
                      state: BridgeState | None = None, in_the_headset: bool = False,
                      **how) -> CommandAnswering:
    config = made_up_bridge_config(tmp_path, vr_main_funestra=in_the_headset)
    state = state or BridgeState()
    return CommandAnswering(
        config, made_up_session_files(tmp_path), state=state,
        window_work=window_work or made_up_window_work(tmp_path),
        opening=OrigeneratorOpening(config, state, already_open=False, showing_a_slideshow=False),
        **{"dashboard_enabled": False, **how})
