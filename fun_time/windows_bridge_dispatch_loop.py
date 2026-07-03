"""Background dispatch loop for dashboard commands and genau sync.

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
from .mode_plan import genau_active
from .modes import build_mirrored_funscript_path
from .windows_bridge_random_favs_browser import open_rfb_tab
from .voice_control import VoiceController
from .dashboard_bridge import write_dashboard_snapshot
from .dashboard_runtime import is_broker_heartbeat_fresh, is_osr2_device_on
from .runtime_flow import read_flag_file
from .windows_bridge_startup import restart_broker, stop_broker_processes
from .vlc_actions import get_playback_time_and_length, send_vlc_input_command, vlc_http_cmd
from .vlc_seek import PrimarySeekAccumulator
from .z_order import apply_z_order, compute_z_order
from .win32 import (
    activate_window,
    find_window_by_pid,
    find_window_by_title,
    hide_window,
    is_window_topmost,
    minimize_window,
    restore_window,
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
        if op.op in ("suspend_hotkeys", "unsuspend_hotkeys", "tooltip",
                      "disable_all_topmost", "restore_all_topmost",
                      "open_rfb_tab"):
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
            hwnd = find_window_by_title(op.title, exact=op.exact)
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
        "primary_mode": state.primary_mode,
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
    raw_mode = s.get("primary_mode", s.get("genau_mode", "nau"))
    # Backward compat: old INI files used "1"/"0" for genau_mode, and Nau
    # replaced the retired vlc mode as the primary player.
    if raw_mode == "1":
        primary_mode = "genau"
    elif raw_mode in ("0", "vlc"):
        primary_mode = "nau"
    else:
        primary_mode = raw_mode
    return BridgeState(
        locked2=s.get("locked2", "0") == "1",
        locked3=s.get("locked3", "0") == "1",
        primary_mode=primary_mode,
        f_mode_enabled=s.get("f_mode_enabled", "0") == "1",
        omni_paused=s.get("omni_paused", "0") == "1",
    )




class DispatchLoopRunner:
    """Runs dashboard polling and genau sync in-process."""

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
        rfb_shortcut_target: str = "",
        rfb_shortcut_work_dir: str = "",
        rfb_shortcut_args: str = "",
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
        self.rfb_shortcut_target = rfb_shortcut_target
        self.rfb_shortcut_work_dir = rfb_shortcut_work_dir
        self.rfb_shortcut_args = rfb_shortcut_args
        self.sync_interval_s = sync_interval_ms / 1000
        self.state = BridgeState()
        self._last_sync = 0.0
        self._stop = threading.Event()
        self._file_dialog_lock = threading.Lock()
        self._press_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._press_port: int | None = None
        self._press_port_file = config.state_dir / "dashboard_press_port.txt"
        self.voice_controller: VoiceController | None = None
        self._seek_accumulator = PrimarySeekAccumulator(
            read_position=lambda: get_playback_time_and_length(
                config.primary_port, config.vlc_password
            ),
            seek=self._seek_primary_absolute,
        )

    _HOTKEY_TO_BUTTON: dict[str, str] = {}

    def _seek_primary_absolute(self, target_seconds: float) -> None:
        """Seek the Primary VLC to an absolute position (seconds)."""
        val = int(round(target_seconds))
        ok = vlc_http_cmd(self.config.primary_port, f"seek&val={val}", self.config.vlc_password)
        logger.info("primary_seek → %ds → %s", val, "ok" if ok else "FAILED")

    def tick(self) -> None:
        """Run one iteration: poll dashboard, maybe sync genau."""
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
            elif cmd == "omniminimize":
                self._handle_omniminimize()
            elif cmd == "omnirestore":
                self._handle_omnirestore()
            elif cmd == "omnipause_toggle":
                self._handle_omnipause_toggle()
            elif cmd == "enter_omnipause":
                if not self.state.omni_paused:
                    self._handle_enter_omnipause()
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
                if genau_active(self.state.primary_mode):
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
            elif cmd == "portrait_lock_off":
                if self.state.locked2:
                    self._dispatch("portrait_lock")
            elif cmd == "landscape_lock_off":
                if self.state.locked3:
                    self._dispatch("landscape_lock")
            elif cmd == "fmode_on":
                if not self.state.f_mode_enabled:
                    self._dispatch("fmode_toggle")
            elif cmd == "fmode_off":
                if self.state.f_mode_enabled:
                    self._dispatch("fmode_toggle")
            elif cmd == "broker_start":
                self._handle_broker_start()
            elif cmd == "broker_stop":
                self._handle_broker_stop()
            elif cmd in ("voice_off", "voice_toggle"):
                self._handle_voice_toggle(cmd)
            elif cmd == "vlc_nudge_next":
                self._seek_accumulator.nudge(1)
            elif cmd == "vlc_nudge_prev":
                self._seek_accumulator.nudge(-1)
            elif cmd in ("primary_prev", "primary_next"):
                # A video change invalidates the running seek target.
                self._seek_accumulator.invalidate()
                self._dispatch(cmd)
            else:
                self._dispatch(cmd)

        # Periodic sync: z-order enforcement and dashboard update
        now = time.monotonic()
        if now - self._last_sync >= self.sync_interval_s:
            self._last_sync = now
            if not self.state.omni_paused:
                self._apply_z_order(reorder=False)
            if self.dashboard_enabled:
                self._update_dashboard()

    def _dispatch(self, command: str) -> None:
        logger.info("Dispatching command: %s", command)
        prev_mode = self.state.primary_mode
        new_state, ops = dispatch_command(command, self.state, self.config)
        self.state = new_state
        remaining = execute_window_ops(ops, self.primary_pid)
        # Any mode change can reshuffle the Primary/Genau stack — compute_z_order
        # gives vlc, genau, and hybrid three distinct stacks — so reorder on the
        # actual mode, not on genau_active() (which collapses genau and hybrid).
        if self.state.primary_mode != prev_mode:
            self._apply_z_order()
        suppress_unsuspend = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
        for op in remaining:
            if op.op == "disable_all_topmost":
                self._remove_all_topmost()
                continue
            if op.op == "restore_all_topmost":
                self._restore_all_topmost()
                continue
            if suppress_unsuspend and op.op == "unsuspend_hotkeys":
                continue
            if op.op == "open_rfb_tab":
                if self.rfb_hwnd and self.rfb_shortcut_target:
                    open_rfb_tab(
                        url=op.key,
                        shortcut_target=self.rfb_shortcut_target,
                        shortcut_work_dir=self.rfb_shortcut_work_dir,
                        shortcut_args=self.rfb_shortcut_args,
                    )
                    logger.info("Opened RFB tab: %s", op.key)
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
            genau_mode_on = read_flag_file(self.config.genau_mode_file, False)
            device_on = is_osr2_device_on(self.config.state_dir / "osr2_serial_rx.txt")
            if not device_on:
                osr2_mode = "off"
            elif genau_mode_on:
                osr2_mode = "auto"
            else:
                osr2_mode = "controlled"
            voice_active = self.voice_controller is not None and not self.voice_controller.is_muted
            write_dashboard_snapshot(
                str(self.config.dashboard_state_file),
                f_mode_enabled=self.state.f_mode_enabled,
                osr2_mode=osr2_mode,
                mfp_alive=bool(self.mfp_pid),
                primary_mode=self.state.primary_mode,
                portrait_locked=self.state.locked2,
                landscape_locked=self.state.locked3,
                omni_paused=self.state.omni_paused,
                voice_active=voice_active,
            )
        except Exception:
            pass

    def _window_layers(self) -> list[tuple[int, bool]]:
        """The centralized (hwnd, should_be_topmost) stack for every managed window.

        Single source of truth for the window roster — z-order application,
        topmost removal, and omniminimize all walk this same list.
        """
        return compute_z_order(
            rfb_hwnd=self.rfb_hwnd,
            portrait_hwnd=find_window_by_pid(self.portrait_pid),
            landscape_hwnd=find_window_by_pid(self.landscape_pid),
            primary_hwnd=find_window_by_pid(self.primary_pid),
            genau_hwnd=find_window_by_title("Genau"),
            mfp_hwnd=find_window_by_pid(self.mfp_pid),
            dashboard_hwnd=self._find_dashboard_hwnd(),
            primary_mode=self.state.primary_mode,
        )

    def _apply_z_order(self, *, reorder: bool = True) -> None:
        """Look up all window HWNDs and apply the centralized z-order stack.

        *reorder=True* (default) rebuilds the full stack (demote all,
        then promote bottom-to-top).  Use after transitions.

        *reorder=False* only corrects drift — demotes windows that
        should not be topmost without touching the rest.  Use for the
        periodic sync tick to avoid visual flicker.
        """
        apply_z_order(self._window_layers(), reorder=reorder)

    def _remove_all_topmost(self) -> None:
        """Demote all windows from the TOPMOST band (omnipause)."""
        apply_z_order([(h, False) for h, _ in self._window_layers()])

    def _restore_all_topmost(self) -> None:
        """Restore the correct z-order stack after omnipause."""
        self._apply_z_order()

    def _is_broker_alive(self) -> bool:
        hb = self.config.broker_heartbeat_file
        return hb is not None and is_broker_heartbeat_fresh(hb)

    def _handle_voice_toggle(self, cmd: str) -> None:
        """Mute or toggle voice control, then refresh the dashboard."""
        if self.voice_controller is None:
            return
        if cmd == "voice_off":
            self.voice_controller.mute()
        elif self.voice_controller.is_muted:
            self.voice_controller.unmute()
        else:
            self.voice_controller.mute()
        if self.dashboard_enabled:
            self._update_dashboard()

    def _handle_broker_toggle(self) -> None:
        """Stop broker if running, start it if stopped."""
        project_dir = self.config.state_dir.parent
        if self._is_broker_alive():
            stop_broker_processes(project_dir)
        else:
            restart_broker(project_dir, self.config.broker_tray_launcher)

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

    def _find_dashboard_hwnd(self) -> int:
        """Find the Dashboard window, falling back to title search.

        The PID-based lookup can fail if the venv launcher's PID differs
        from the actual Python interpreter process that owns the Qt window.
        """
        hwnd = find_window_by_pid(self.dashboard_pid) if self.dashboard_pid else 0
        if not hwnd:
            hwnd = find_window_by_title("Fun Time")
            if hwnd:
                logger.info(
                    "Dashboard found by title (hwnd=%d) but NOT by pid %d",
                    hwnd, self.dashboard_pid,
                )
        return hwnd

    def _handle_omniminimize(self) -> None:
        """Minimize every managed window — the "omniminimize" command.

        Walks the same window roster as the z-order stack (RFB, the three
        VLCs, Genau, MFP, and the dashboard itself) and minimizes each with
        ``activate=False`` so minimizing one never yanks focus to the next.
        """
        for hwnd, _topmost in self._window_layers():
            minimize_window(hwnd, activate=False)

    def _handle_omnirestore(self) -> None:
        """Un-minimize every managed window — the mirror of omniminimize.

        Restores each window with ``activate=False`` to avoid focus churn,
        then re-applies the z-order so the stack comes back in the right order.
        """
        for hwnd, _topmost in self._window_layers():
            restore_window(hwnd, activate=False)
        self._apply_z_order()

    def _handle_omnipause_toggle(self) -> None:
        """Toggle omnipause with topmost management for all windows.

        Topmost removal (enter) and restoration (leave) are driven by
        the disable_all_topmost / restore_all_topmost WindowOps that
        command_dispatch emits — _dispatch handles them automatically.
        """
        if self.state.omni_paused:
            dash_hwnd = self._find_dashboard_hwnd()
            logger.info(
                "Un-omnipause pre-restore: dash_hwnd=%d dash_topmost=%s "
                "rfb_hwnd=%d rfb_topmost=%s",
                dash_hwnd, is_window_topmost(dash_hwnd) if dash_hwnd else "N/A",
                self.rfb_hwnd, is_window_topmost(self.rfb_hwnd) if self.rfb_hwnd else "N/A",
            )
        self._dispatch("omnipause_toggle")

    def _handle_enter_omnipause(self) -> None:
        """Enter omnipause with topmost management (Space key — enter only, no leave).

        Topmost removal is driven by the disable_all_topmost WindowOp
        that command_dispatch emits — _dispatch handles it automatically.
        """
        self._dispatch("enter_omnipause")

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

        try:
            default_dir = self.config.primary_sources.split("|")[0] if self.config.primary_sources else ""
            primary_hwnd = find_window_by_pid(self.primary_pid)
            selected = show_open_file_dialog(default_dir or "", owner_hwnd=primary_hwnd)
            if selected:
                if self.state.primary_mode == "hybrid":
                    send_vlc_input_command(
                        self.config.primary_port,
                        "in_play",
                        selected,
                        self.config.vlc_password,
                    )
                    vlc_http_cmd(self.config.primary_port, "pl_play", self.config.vlc_password)
                else:
                    mirrored = build_mirrored_funscript_path(selected)
                    if mirrored and Path(mirrored).exists():
                        command = f"PLAY_FILE {selected}\t{mirrored}"
                    else:
                        command = f"PLAY_FILE {selected}"
                    self.config.nau_cmd_file.write_text(command, encoding="utf-8")
        finally:
            if should_manage_omnipause:
                self._dispatch("leave_omnipause_skip_primary")

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
        genau_mode_file=Path(manifest["commands"]["genau_mode_file"]),
        genau_cmd_file=Path(manifest["commands"]["genau_cmd_file"]),
        genau_paused_file=Path(manifest["commands"]["genau_paused_file"]),
        audio_paused_file=Path(manifest["commands"]["audio_paused_file"]),
        nau_cmd_file=Path(manifest["commands"]["nau_cmd_file"]),
        nau_paused_file=Path(manifest["commands"]["nau_paused_file"]),
        nau_status_file=Path(manifest["commands"]["nau_status_file"]),
        dashboard_state_file=Path(manifest["commands"]["dashboard_state_file"]),
        broker_cmd_file=Path(manifest["commands"]["broker_cmd_file"]),
        broker_heartbeat_file=Path(manifest["commands"]["broker_heartbeat_file"]),
        broker_tray_launcher=Path(v) if (v := manifest["commands"].get("broker_tray_launcher", "").strip()) else None,
        provider_media_root=Path(v) if (v := manifest.get("provider_regen", "media_root", fallback="").strip()) else None,
        provider_metadata_root=Path(v) if (v := manifest.get("provider_regen", "metadata_root", fallback="").strip()) else None,
        provider_generate_video_url=manifest.get("provider_regen", "generate_video_url", fallback="https://example.com/video"),
        provider_generate_image_url=manifest.get("provider_regen", "generate_image_url", fallback="https://example.com/create"),
    )
