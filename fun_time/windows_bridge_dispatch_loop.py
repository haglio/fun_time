"""Background dispatch loop for dashboard commands and robot hand sync.

Runs in a thread alongside the AHK hotkey script, handling periodic
dispatch directly in Python instead of spawning subprocesses.
"""
from __future__ import annotations

import configparser
import logging
import os
import socket
import threading
import time
from pathlib import Path

from .command_dispatch import BridgeConfig, BridgeState, WindowOp, dispatch_command
from .dashboard_bridge import write_dashboard_snapshot
from .dashboard_runtime import is_broker_heartbeat_fresh, is_osr2_device_on
from .runtime_flow import read_flag_file
from .windows_bridge_startup import restart_broker, stop_broker_processes
from .vlc_actions import send_vlc_input_command, vlc_http_cmd
from .win32 import (
    activate_window,
    find_window_by_pid,
    find_window_by_title,
    hide_window,
    is_window_topmost,
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
        "genau_mode": "1" if state.genau_mode else "0",
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
        genau_mode=s.get("genau_mode", "0") == "1",
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
        genau_pid: int = 0,
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
        self.genau_pid = genau_pid
        self.sync_interval_s = sync_interval_ms / 1000
        self.state = BridgeState()
        self._last_sync = 0.0
        self._stop = threading.Event()
        self._file_dialog_lock = threading.Lock()
        self._genau_activate_pending = False
        self._press_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._press_port: int | None = None
        self._press_port_file = config.state_dir / "dashboard_press_port.txt"

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
            self._send_press(button)
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
            elif cmd == "broker_panel":
                threading.Thread(
                    target=self._handle_broker_toggle,
                    daemon=True,
                    name="broker-toggle",
                ).start()
            elif cmd == "backslash_key":
                if self.state.genau_mode:
                    self._send_press("quarter_button")
                    self._dispatch("quarter_button")
                else:
                    self._send_press("open_file_dialog")
                    threading.Thread(
                        target=self._handle_open_file_dialog,
                        daemon=True,
                        name="file-dialog",
                    ).start()
            # -- idempotent voice commands --
            elif cmd == "pause":
                if not self.state.omni_paused:
                    self._handle_omnipause_toggle()
            elif cmd == "play":
                if self.state.omni_paused:
                    self._handle_omnipause_toggle()
            elif cmd == "portrait_lock_on":
                if not self.state.locked2:
                    self._dispatch("portrait_lock")
            elif cmd == "landscape_lock_on":
                if not self.state.locked3:
                    self._dispatch("landscape_lock")
            elif cmd == "fmode_on":
                if not self.state.f_mode_enabled:
                    self._dispatch("fmode_toggle")
            elif cmd == "fmode_off":
                if self.state.f_mode_enabled:
                    self._dispatch("fmode_toggle")
            elif cmd == "genau_enable":
                if not read_flag_file(self.config.genau_enabled_file, True):
                    self._dispatch("robot_toggle")
            elif cmd == "genau_disable":
                if read_flag_file(self.config.genau_enabled_file, True):
                    self._dispatch("robot_toggle")
            elif cmd == "broker_start":
                self._handle_broker_start()
            elif cmd == "broker_stop":
                self._handle_broker_stop()
            else:
                self._dispatch(cmd)

        # Robot hand sync (skip while omni-paused)
        now = time.monotonic()
        if now - self._last_sync >= self.sync_interval_s:
            self._last_sync = now
            if not self.state.omni_paused:
                prev_mode = self.state.genau_mode
                self._dispatch("sync_genau")
                if self.state.genau_mode and not prev_mode:
                    self._genau_activate_pending = True
                if self.state.genau_mode:
                    self._enforce_genau_z_order()

        self._try_genau_activate()

    def _try_genau_activate(self) -> None:
        if not self._genau_activate_pending:
            return
        if not self.state.genau_mode:
            self._genau_activate_pending = False
            return
        hwnd = find_window_by_title("Genau")
        if not hwnd:
            return
        set_always_on_top(hwnd, True)
        if os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1":
            activate_window(hwnd)
        self._genau_activate_pending = False

    def _enforce_genau_z_order(self) -> None:
        """Keep Primary VLC out of the TOPMOST band while Genau is active.

        VLC may re-assert topmost during video transitions; demoting it
        to the regular z-band guarantees Genau stays above it.
        """
        primary_hwnd = find_window_by_pid(self.primary_pid)
        if primary_hwnd:
            set_always_on_top(primary_hwnd, False)
        robot_hwnd = find_window_by_title("Genau")
        if robot_hwnd:
            set_always_on_top(robot_hwnd, True)

    def _dispatch(self, command: str) -> None:
        prev_genau = self.state.genau_mode
        new_state, ops = dispatch_command(command, self.state, self.config)
        self.state = new_state
        remaining = execute_window_ops(ops, self.primary_pid)
        if self.state.genau_mode != prev_genau:
            primary_hwnd = find_window_by_pid(self.primary_pid)
            if primary_hwnd:
                set_always_on_top(primary_hwnd, not self.state.genau_mode)
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

    def _send_press(self, action: str) -> None:
        if not self.dashboard_enabled:
            return
        try:
            if self._press_port is None:
                if self._press_port_file.exists():
                    self._press_port = int(self._press_port_file.read_text(encoding="utf-8").strip())
            if self._press_port is not None:
                self._press_socket.sendto(action.encode("utf-8"), ("127.0.0.1", self._press_port))
        except (OSError, ValueError):
            pass

    def _update_dashboard(self) -> None:
        try:
            robot_link_enabled = read_flag_file(self.config.genau_enabled_file, True)
            genau_mode_on = read_flag_file(self.config.genau_mode_file, False)
            device_on = is_osr2_device_on(self.config.state_dir / "osr2_serial_rx.txt")
            if not device_on:
                osr2_mode = "off"
            elif genau_mode_on:
                osr2_mode = "auto"
            else:
                osr2_mode = "controlled"
            write_dashboard_snapshot(
                str(self.config.dashboard_state_file),
                f_mode_enabled=self.state.f_mode_enabled,
                robot_link_enabled=robot_link_enabled,
                osr2_mode=osr2_mode,
                mfp_alive=bool(self.mfp_pid),
                primary_uses_genau=self.state.genau_mode and robot_link_enabled,
                portrait_locked=self.state.locked2,
                landscape_locked=self.state.locked3,
                omni_paused=self.state.omni_paused,
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
        if self.genau_pid:
            hwnd = find_window_by_pid(self.genau_pid)
            if hwnd:
                set_always_on_top(hwnd, False)

    def _restore_all_topmost(self) -> None:
        # Mirror the startup z-order sequence in windows_bridge_sequencer.py:
        # 1. RFB first (so it ends up below everything set after it)
        # 2. Non-dashboard PIDs
        # 3. Dashboard toggle (False→True) — NEVER a bare True
        # 4. Genau last (on top of all) if in genau mode
        genau_mode = self.state.genau_mode

        if self.rfb_hwnd:
            set_always_on_top(self.rfb_hwnd, True)

        for pid in self._all_pids:
            if pid == self.dashboard_pid:
                continue  # handled separately below
            hwnd = find_window_by_pid(pid)
            if not hwnd:
                continue
            if pid == self.primary_pid and genau_mode:
                set_always_on_top(hwnd, False)
                continue
            set_always_on_top(hwnd, True)

        # Re-assert Dashboard topmost with a toggle (False→True).
        # Dashboard manages its own WS_EX_TOPMOST via Qt's
        # WindowStaysOnTopHint, which may re-assert topmost between
        # _remove_all_topmost and here.  A bare HWND_TOPMOST is a
        # no-op on an already-topmost window, so the toggle forces
        # Windows to reinsert Dashboard at the front of the topmost
        # band.  This matches the startup sequence exactly.
        if self.dashboard_pid:
            dash_hwnd = find_window_by_pid(self.dashboard_pid)
            if dash_hwnd:
                set_always_on_top(dash_hwnd, False)
                set_always_on_top(dash_hwnd, True)
                logger.debug(
                    "Dashboard topmost toggled: hwnd=%d, now_topmost=%s",
                    dash_hwnd, is_window_topmost(dash_hwnd),
                )
            else:
                logger.warning(
                    "Dashboard hwnd not found for pid %d during topmost restore",
                    self.dashboard_pid,
                )
        # Log RFB state for diagnostics
        if self.rfb_hwnd:
            logger.debug(
                "RFB topmost state after restore: hwnd=%d, topmost=%s",
                self.rfb_hwnd, is_window_topmost(self.rfb_hwnd),
            )

        if genau_mode and self.genau_pid:
            hwnd = find_window_by_pid(self.genau_pid)
            if hwnd:
                set_always_on_top(hwnd, True)

    def _is_broker_alive(self) -> bool:
        hb = self.config.broker_heartbeat_file
        return hb is not None and is_broker_heartbeat_fresh(hb)

    def _handle_broker_toggle(self) -> None:
        """Stop broker if running, start it if stopped."""
        project_dir = self.config.state_dir.parent
        if self._is_broker_alive():
            stop_broker_processes(project_dir)
        else:
            restart_broker(project_dir)

    def _handle_broker_start(self) -> None:
        """Start broker only if not already running."""
        if not self._is_broker_alive():
            threading.Thread(
                target=lambda: restart_broker(self.config.state_dir.parent),
                daemon=True,
                name="broker-start",
            ).start()

    def _handle_broker_stop(self) -> None:
        """Stop broker only if currently running."""
        if self._is_broker_alive():
            threading.Thread(
                target=lambda: stop_broker_processes(self.config.state_dir.parent),
                daemon=True,
                name="broker-stop",
            ).start()

    def _handle_omnipause_toggle(self) -> None:
        """Toggle omnipause with topmost management for all windows."""
        was_paused = self.state.omni_paused
        if was_paused:
            # Pre-restore diagnostic: check if Qt re-asserted topmost
            # during omnipause (Dashboard has WindowStaysOnTopHint).
            dash_hwnd = find_window_by_pid(self.dashboard_pid) if self.dashboard_pid else 0
            logger.info(
                "Un-omnipause pre-restore: dash_hwnd=%d dash_topmost=%s "
                "rfb_hwnd=%d rfb_topmost=%s",
                dash_hwnd, is_window_topmost(dash_hwnd) if dash_hwnd else "N/A",
                self.rfb_hwnd, is_window_topmost(self.rfb_hwnd) if self.rfb_hwnd else "N/A",
            )
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
        primary_port=int(manifest["vlc"]["primary_vlc_port"]),
        portrait_port=int(manifest["vlc"]["vlc2_port"]),
        landscape_port=int(manifest["vlc"]["vlc3_port"]),
        vlc_password=manifest["vlc"]["vlc_pass"],
        favs_file=Path(manifest["media"]["favs_file"]),
        weird_dir=Path(manifest["media"]["weird_dir"]),
        state_dir=Path(manifest["commands"]["dashboard_state_file"]).parent,
        primary_sources=manifest["media"]["primary_vlc_sources"],
        portrait_sources=manifest["media"]["portrait_dirs"],
        landscape_sources=manifest["media"]["landscape_dirs"],
        genau_enabled_file=Path(manifest["commands"]["genau_enabled_file"]),
        genau_mode_file=Path(manifest["commands"]["genau_mode_file"]),
        genau_cmd_file=Path(manifest["commands"]["genau_cmd_file"]),
        genau_paused_file=Path(manifest["commands"]["genau_paused_file"]),
        audio_paused_file=Path(manifest["commands"]["audio_paused_file"]),
        dashboard_state_file=Path(manifest["commands"]["dashboard_state_file"]),
        broker_heartbeat_file=Path(manifest["commands"]["broker_heartbeat_file"]),
    )
