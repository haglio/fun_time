"""Background dispatch loop for dashboard commands and robot hand sync.

Runs in a thread alongside the AHK hotkey script, handling periodic
dispatch directly in Python instead of spawning subprocesses.
"""
from __future__ import annotations

import configparser
import logging
import threading
import time
from pathlib import Path

from .bridge_command_dispatch import BridgeConfig, BridgeState, WindowOp, dispatch_command
from .windows_bridge_dashboard_bridge import write_dashboard_snapshot
from .windows_bridge_runtime_flow import read_flag_file
from .windows_bridge_win32 import (
    activate_window,
    find_dialog_by_pid,
    find_window_by_pid,
    find_window_by_title,
    hide_window,
    send_ctrl_o,
    send_key_to_window,
    set_always_on_top,
    show_window,
    wait_for_window_close,
)

logger = logging.getLogger(__name__)


def poll_dashboard_command(cmd_file: Path) -> str | None:
    """Read and delete the dashboard command file, returning the command or None."""
    if not cmd_file.exists():
        return None
    try:
        text = cmd_file.read_text(encoding="utf-8").strip()
        cmd_file.unlink()
    except OSError:
        return None
    return text or None


def execute_window_ops(ops: list[WindowOp], primary_pid: int) -> list[WindowOp]:
    """Execute window operations via Python win32, returning any that need AHK."""
    remaining: list[WindowOp] = []
    for op in ops:
        if op.op in ("suspend_hotkeys", "unsuspend_hotkeys"):
            remaining.append(op)
            continue

        if op.op == "send_key":
            hwnd = find_window_by_pid(primary_pid)
            if hwnd:
                send_key_to_window(hwnd, op.key)
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
            activate_window(hwnd)
        elif op.op == "show":
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


# Commands that need AHK's Suspend built-in — forward rather than dispatch.
_AHK_ONLY_COMMANDS = frozenset({"omnipause_toggle"})


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
        self.sync_interval_s = sync_interval_ms / 1000
        self.state = BridgeState()
        self._last_sync = 0.0
        self._ahk_cmd_pending_until = 0.0
        self._stop = threading.Event()
        self._file_dialog_lock = threading.Lock()

    def tick(self) -> None:
        """Run one iteration: poll dashboard, maybe sync robot hand."""
        # Sync state from shared file — AHK hotkey dispatches update it directly.
        shared = read_shared_state(self.shared_state_file)
        if shared is not None:
            self.state = shared

        # Dashboard command
        cmd = poll_dashboard_command(self.dashboard_cmd_file)
        if cmd:
            if cmd in _AHK_ONLY_COMMANDS:
                self.ahk_cmd_file.write_text(cmd, encoding="utf-8")
                # Suppress sync for a few seconds while AHK processes the
                # command — prevents overwriting shared state with stale values.
                self._ahk_cmd_pending_until = time.monotonic() + 5.0
            elif cmd == "open_file_dialog":
                threading.Thread(
                    target=self._handle_open_file_dialog,
                    daemon=True,
                    name="file-dialog",
                ).start()
            else:
                self._dispatch(cmd)

        # Robot hand sync (skip while omni-paused or AHK command pending)
        now = time.monotonic()
        if now - self._last_sync >= self.sync_interval_s:
            self._last_sync = now
            if not self.state.omni_paused and now >= self._ahk_cmd_pending_until:
                self._dispatch("sync_robot_hand")

    def _dispatch(self, command: str) -> None:
        new_state, ops = dispatch_command(command, self.state, self.config)
        self.state = new_state
        execute_window_ops(ops, self.primary_pid)
        write_shared_state(self.shared_state_file, self.state)
        if self.dashboard_enabled:
            self._update_dashboard()

    def _update_dashboard(self) -> None:
        try:
            robot_link_enabled = read_flag_file(self.config.robot_hand_enabled_file, True)
            robot_hand_mode_on = read_flag_file(self.config.robot_hand_mode_file, False)
            write_dashboard_snapshot(
                str(self.config.dashboard_state_file),
                f_mode_enabled=self.state.f_mode_enabled,
                robot_link_enabled=robot_link_enabled,
                osr2_mode="auto" if robot_hand_mode_on else "controlled",
                mfp_alive=bool(self.mfp_pid),
                primary_uses_robot_hand=self.state.robot_hand_mode and robot_link_enabled,
                portrait_locked=self.state.locked2,
                landscape_locked=self.state.locked3,
            )
        except Exception:
            pass

    def _handle_open_file_dialog(self) -> None:
        """Open VLC's file dialog with managed omnipause.

        Replaces AHK's OpenPrimaryVlcFileDialogWithManagedOmniPause:
        1. If not already paused: enter omnipause, suspend hotkeys, remove topmost
        2. Activate primary VLC and send Ctrl+O
        3. If entered omnipause: wait for the file dialog to close
        4. Finally: leave omnipause, unsuspend hotkeys, restore topmost
        """
        if not self._file_dialog_lock.acquire(blocking=False):
            return
        try:
            self._handle_open_file_dialog_inner()
        finally:
            self._file_dialog_lock.release()

    def _handle_open_file_dialog_inner(self) -> None:
        should_manage_omnipause = not self.state.omni_paused
        all_pids = [self.primary_pid, self.portrait_pid, self.landscape_pid,
                    self.mfp_pid, self.dashboard_pid]

        if should_manage_omnipause:
            self._dispatch("enter_omnipause")
            self.ahk_cmd_file.write_text("suspend_hotkeys", encoding="utf-8")
            for pid in all_pids:
                hwnd = find_window_by_pid(pid)
                if hwnd:
                    set_always_on_top(hwnd, False)

        try:
            primary_hwnd = find_window_by_pid(self.primary_pid)
            if primary_hwnd:
                activate_window(primary_hwnd)
                time.sleep(0.05)
            send_ctrl_o()

            if should_manage_omnipause:
                dialog_hwnd = find_dialog_by_pid(self.primary_pid, timeout_s=1.0)
                if dialog_hwnd:
                    wait_for_window_close(dialog_hwnd)
        finally:
            if should_manage_omnipause:
                self._dispatch("leave_omnipause_skip_primary")
                robot_hand_mode = self.state.robot_hand_mode
                for pid in all_pids:
                    if pid == self.primary_pid and robot_hand_mode:
                        continue
                    hwnd = find_window_by_pid(pid)
                    if hwnd:
                        set_always_on_top(hwnd, True)
                self.ahk_cmd_file.write_text("unsuspend_hotkeys", encoding="utf-8")

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
