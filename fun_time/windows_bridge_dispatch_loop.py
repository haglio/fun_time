"""Background dispatch loop for dashboard commands and robot hand sync.

Runs in a thread alongside the AHK hotkey script, handling periodic
dispatch directly in Python instead of spawning subprocesses.
"""
from __future__ import annotations

import configparser
import logging
import os
import threading
import time
from pathlib import Path

from .command_dispatch import BridgeConfig, BridgeState, WindowOp, dispatch_command
from .dashboard_bridge import write_dashboard_snapshot
from .runtime_flow import read_flag_file
from .vlc_actions import send_vlc_input_command, vlc_http_cmd
from .win32 import (
    activate_window,
    find_window_by_pid,
    find_window_by_title,
    hide_window,
    send_vk_to_window,
    send_key_to_window,
    set_always_on_top,
    show_open_file_dialog,
    show_window,
)

logger = logging.getLogger(__name__)


def poll_dashboard_commands(cmd_file: Path) -> list[str]:
    """Read and delete the dashboard command file, returning all queued commands."""
    if not cmd_file.exists():
        return []
    try:
        # Atomically move then read — any concurrent writes create a new file.
        # replace() is used instead of rename() because on Windows rename()
        # fails if the target already exists (e.g. stale .processing from a crash).
        tmp = cmd_file.with_suffix(".processing")
        cmd_file.replace(tmp)
        text = tmp.read_text(encoding="utf-8-sig").strip()
        tmp.unlink()
    except OSError:
        return []
    if not text:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def execute_window_ops(ops: list[WindowOp], primary_pid: int) -> list[WindowOp]:
    """Execute window operations via Python win32, returning any that need AHK."""
    remaining: list[WindowOp] = []
    for op in ops:
        if op.op in ("suspend_hotkeys", "unsuspend_hotkeys", "tooltip"):
            remaining.append(op)
            continue

        if op.op == "send_key":
            hwnd = find_window_by_pid(primary_pid)
            if hwnd:
                send_key_to_window(hwnd, op.key)
            continue

        if op.op == "send_vk":
            hwnd = find_window_by_pid(primary_pid)
            if hwnd:
                send_vk_to_window(hwnd, op.vk)
            continue

        if op.title:
            hwnd = find_window_by_title(op.title)
            if not hwnd:
                continue
        else:
            continue

        if op.op == "set_topmost":
            set_always_on_top(hwnd, op.value)
        elif op.op == "activate":
            if os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1":
                activate_window(hwnd)
        elif op.op == "show":
            if os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1":
                show_window(hwnd)
        elif op.op == "hide":
            hide_window(hwnd)

    return remaining


def write_shared_state(state_file: Path, state: BridgeState) -> None:
    """Write bridge state to a shared INI file for AHK to read."""
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["state"] = {
        "locked2": "1" if state.locked2 else "0",
        "locked3": "1" if state.locked3 else "0",
        "robot_hand_mode": "1" if state.robot_hand_mode else "0",
        "f_mode_enabled": "1" if state.f_mode_enabled else "0",
        "omni_paused": "1" if state.omni_paused else "0",
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    tmp.replace(state_file)


def read_shared_state(state_file: Path) -> BridgeState | None:
    """Read bridge state from the shared INI file."""
    if not state_file.exists():
        return None
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(state_file), encoding="utf-8")
    if "state" not in parser:
        return None
    s = parser["state"]
    return BridgeState(
        locked2=s.get("locked2", "0") == "1",
        locked3=s.get("locked3", "0") == "1",
        robot_hand_mode=s.get("robot_hand_mode", "0") == "1",
        f_mode_enabled=s.get("f_mode_enabled", "0") == "1",
        omni_paused=s.get("omni_paused", "0") == "1",
    )




class DispatchLoopRunner:
    """Runs dashboard polling and robot hand sync in-process."""

    def __init__(
        self,
        *,
        config: BridgeConfig,
        dashboard_cmd_file: Path,
        shared_state_file: Path,
        ahk_cmd_file: Path,
        primary_pid: int,
        mfp_pid: int,
        portrait_pid: int = 0,
        landscape_pid: int = 0,
        dashboard_pid: int = 0,
        dashboard_enabled: bool,
        rfb_hwnd: int = 0,
        sync_interval_ms: int = 200,
    ) -> None:
        self.config = config
        self.dashboard_cmd_file = dashboard_cmd_file
        self.shared_state_file = shared_state_file
        self.ahk_cmd_file = ahk_cmd_file
        self.primary_pid = primary_pid
        self.mfp_pid = mfp_pid
        self.portrait_pid = portrait_pid
        self.landscape_pid = landscape_pid
        self.dashboard_pid = dashboard_pid
        self.dashboard_enabled = dashboard_enabled
        self.rfb_hwnd = rfb_hwnd
        self.sync_interval_s = sync_interval_ms / 1000
        self.state = BridgeState()
        self._last_sync = 0.0
        self._stop = threading.Event()
        self._file_dialog_lock = threading.Lock()
        self._robot_hand_activate_pending = False
        self._last_press_action = ""
        self._last_press_time = 0.0

    _HOTKEY_TO_BUTTON = {
        "robot_toggle": "link_toggle",
    }

    def tick(self) -> None:
        """Run one iteration: poll dashboard, maybe sync robot hand."""
        # Sync state from shared file — AHK hotkey dispatches update it directly.
        shared = read_shared_state(self.shared_state_file)
        if shared is not None:
            self.state = shared

        # Dashboard commands (may be multiple if queued by rapid hotkey presses)
        for cmd in poll_dashboard_commands(self.dashboard_cmd_file):
            button = self._HOTKEY_TO_BUTTON.get(cmd, cmd)
            self._last_press_action = button
            self._last_press_time = time.time()
            if cmd == "quit":
                self.ahk_cmd_file.write_text("exit", encoding="utf-8")
                continue
            elif cmd == "omnipause_toggle":
                self._handle_omnipause_toggle()
            elif cmd == "open_file_dialog":
                threading.Thread(
                    target=self._handle_open_file_dialog,
                    daemon=True,
                    name="file-dialog",
                ).start()
            elif cmd == "backslash_key":
                if self.state.robot_hand_mode:
                    self._dispatch("quarter_button")
                else:
                    threading.Thread(
                        target=self._handle_open_file_dialog,
                        daemon=True,
                        name="file-dialog",
                    ).start()
            else:
                self._dispatch(cmd)

        # Robot hand sync (skip while omni-paused)
        now = time.monotonic()
        if now - self._last_sync >= self.sync_interval_s:
            self._last_sync = now
            if not self.state.omni_paused:
                prev_mode = self.state.robot_hand_mode
                self._dispatch("sync_robot_hand")
                if self.state.robot_hand_mode and not prev_mode:
                    self._robot_hand_activate_pending = True

        self._try_robot_hand_activate()

    def _try_robot_hand_activate(self) -> None:
        if not self._robot_hand_activate_pending:
            return
        if not self.state.robot_hand_mode:
            self._robot_hand_activate_pending = False
            return
        hwnd = find_window_by_title("Robot Hand")
        if not hwnd:
            return
        show_window(hwnd)
        set_always_on_top(hwnd, True)
        if os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1":
            activate_window(hwnd)
        self._robot_hand_activate_pending = False

    def _dispatch(self, command: str) -> None:
        new_state, ops = dispatch_command(command, self.state, self.config)
        self.state = new_state
        remaining = execute_window_ops(ops, self.primary_pid)
        suppress_unsuspend = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
        for op in remaining:
            if suppress_unsuspend and op.op == "unsuspend_hotkeys":
                continue
            if op.op == "tooltip":
                self.ahk_cmd_file.write_text(f"tooltip {op.key}", encoding="utf-8")
            else:
                self.ahk_cmd_file.write_text(op.op, encoding="utf-8")
        write_shared_state(self.shared_state_file, self.state)
        if self.dashboard_enabled:
            self._update_dashboard()

    def _update_dashboard(self) -> None:
        try:
            robot_link_enabled = read_flag_file(self.config.robot_hand_enabled_file, True)
            robot_hand_mode_on = read_flag_file(self.config.robot_hand_mode_file, False)
            press_action = self._last_press_action
            press_time = self._last_press_time
            if press_action and time.time() - press_time > 0.6:
                self._last_press_action = ""
                self._last_press_time = 0.0
                press_action = ""
                press_time = 0.0
            write_dashboard_snapshot(
                str(self.config.dashboard_state_file),
                f_mode_enabled=self.state.f_mode_enabled,
                robot_link_enabled=robot_link_enabled,
                osr2_mode="auto" if robot_hand_mode_on else "controlled",
                mfp_alive=bool(self.mfp_pid),
                primary_uses_robot_hand=self.state.robot_hand_mode and robot_link_enabled,
                portrait_locked=self.state.locked2,
                landscape_locked=self.state.locked3,
                omni_paused=self.state.omni_paused,
                last_press_action=press_action,
                last_press_time=press_time,
            )
        except Exception:
            pass

    @property
    def _all_pids(self) -> list[int]:
        return [self.primary_pid, self.portrait_pid, self.landscape_pid,
                self.mfp_pid, self.dashboard_pid]

    def _remove_all_topmost(self) -> None:
        # Remove RFB first — the last HWND_NOTOPMOST call wins z-position
        # in the non-topmost band, so RFB must be removed before MFP/Dashboard
        # to preserve relative order (RFB below both).
        if self.rfb_hwnd:
            set_always_on_top(self.rfb_hwnd, False)
        for pid in self._all_pids:
            hwnd = find_window_by_pid(pid)
            if hwnd:
                set_always_on_top(hwnd, False)

    def _restore_all_topmost(self) -> None:
        # Restore RFB first — within the topmost z-band the last window to
        # receive HWND_TOPMOST goes to the front, so RFB must be set before
        # MFP and Dashboard to end up below them.
        if self.rfb_hwnd:
            set_always_on_top(self.rfb_hwnd, True)
        robot_hand_mode = self.state.robot_hand_mode
        for pid in self._all_pids:
            if pid == self.primary_pid and robot_hand_mode:
                continue
            hwnd = find_window_by_pid(pid)
            if hwnd:
                set_always_on_top(hwnd, True)

    def _handle_omnipause_toggle(self) -> None:
        """Toggle omnipause with topmost management for all windows."""
        was_paused = self.state.omni_paused
        self._dispatch("omnipause_toggle")
        if not was_paused:
            self._remove_all_topmost()
        else:
            self._restore_all_topmost()

    def _handle_open_file_dialog(self) -> None:
        """Open VLC's file dialog with managed omnipause."""
        if not self._file_dialog_lock.acquire(blocking=False):
            return
        try:
            self._handle_open_file_dialog_inner()
        finally:
            self._file_dialog_lock.release()

    def _handle_open_file_dialog_inner(self) -> None:
        should_manage_omnipause = not self.state.omni_paused

        if should_manage_omnipause:
            self._dispatch("enter_omnipause")
            self._remove_all_topmost()

        try:
            default_dir = self.config.primary_sources.split("|")[0] if self.config.primary_sources else ""
            primary_hwnd = find_window_by_pid(self.primary_pid)
            selected = show_open_file_dialog(default_dir or "", owner_hwnd=primary_hwnd)
            if selected:
                send_vlc_input_command(
                    self.config.primary_port,
                    "in_play",
                    selected,
                    self.config.vlc_password,
                )
                vlc_http_cmd(self.config.primary_port, "pl_play", self.config.vlc_password)
        finally:
            if should_manage_omnipause:
                self._dispatch("leave_omnipause_skip_primary")
                self._restore_all_topmost()

    def run(self) -> None:
        """Main loop — call from a background thread."""
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Dispatch loop error")
            self._stop.wait(0.05)

    def stop(self) -> None:
        self._stop.set()


def build_bridge_config_from_manifest(
    manifest: configparser.ConfigParser,
) -> BridgeConfig:
    """Build a BridgeConfig from the windows bridge manifest INI."""
    return BridgeConfig(
        primary_port=int(manifest["controller"]["primary_vlc_port"]),
        portrait_port=int(manifest["controller"]["vlc2_port"]),
        landscape_port=int(manifest["controller"]["vlc3_port"]),
        vlc_password=manifest["controller"]["vlc_pass"],
        favs_file=Path(manifest["media"]["favs_file"]),
        weird_dir=Path(manifest["media"]["weird_dir"]),
        state_dir=Path(manifest["commands"]["dashboard_state_file"]).parent,
        primary_sources=manifest["media"]["primary_vlc_sources"],
        portrait_sources=manifest["media"]["portrait_dirs"],
        landscape_sources=manifest["media"]["landscape_dirs"],
        robot_hand_enabled_file=Path(manifest["commands"]["robot_hand_enabled_file"]),
        robot_hand_mode_file=Path(manifest["commands"]["robot_hand_mode_file"]),
        robot_hand_cmd_file=Path(manifest["commands"]["robot_hand_cmd_file"]),
        robot_hand_paused_file=Path(manifest["commands"]["robot_hand_paused_file"]),
        audio_paused_file=Path(manifest["commands"]["audio_paused_file"]),
        dashboard_state_file=Path(manifest["commands"]["dashboard_state_file"]),
    )
