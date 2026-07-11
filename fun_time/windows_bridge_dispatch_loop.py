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

from .audio_volume import MAX_VOLUME
from .command_dispatch import BridgeConfig, BridgeState, WindowOp, command_side, dispatch_command
from .event_log import notice
from .mode_plan import genau_active
from .modes import build_mirrored_funscript_path
from .video_timeline import VideoTimeline
from .vlc_actions import get_current_file_path, get_playback_fraction
from .voice_commands import parse_command_line
from .watch_stats import WatchTracker, record_watch_event, watch_stats_path
from .windows_bridge_random_favs_browser import open_rfb_tab
from .voice_control import VoiceController
from .dashboard_bridge import write_dashboard_snapshot
from .dashboard_runtime import is_broker_heartbeat_fresh, is_osr2_device_on, read_nau_status
from .runtime_flow import read_flag_file
from .windows_bridge_startup import restart_broker, stop_broker_processes
from .window_roles import (
    FIXED_TOPMOST_ROLES,
    LOG_PANEL_WINDOW_TITLE,
    MANAGED_ROLES,
    role_topmost,
)
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


def resolve_active_side_command(command: str, active_side: int) -> str:
    """Rewrite a side-agnostic ``active_*`` command onto the active player.

    ``active_next``/``active_prev`` follow the last player navigated — primary
    (Nau, slot 1), portrait (2), or landscape (3).  The other actions (lock,
    weird, cycle) exist only on the satellites, so while the primary is active
    they resolve to nothing — returned unchanged, which is a no-op downstream.
    Every non-``active_`` command passes through unchanged.
    """
    if not command.startswith("active_"):
        return command
    action = command[len("active_"):]
    if active_side == 1:  # primary (Nau) participates in navigation only
        if action in ("next", "prev"):
            return f"primary_{action}"
        return command
    prefix = "portrait_" if active_side == 2 else "landscape_"
    return prefix + action


def expand_both_command(command: str) -> list[str]:
    """Expand a ``both_*`` command into its Portrait + Landscape pair.

    Saying "both next" enqueues ``both_next``; there is no combined handler —
    a both-command is just sugar for driving each satellite in turn (Portrait
    first) through the exact same per-command handling as "portrait next" /
    "landscape next".  Any other command passes through unchanged.
    """
    if command.startswith("both_"):
        suffix = command[len("both_"):]
        return [f"portrait_{suffix}", f"landscape_{suffix}"]
    return [command]


def execute_window_ops(ops: list[WindowOp], nau_pid: int) -> list[WindowOp]:
    """Execute window operations via Python win32, returning any that need AHK.

    ``send_key``/``send_vk`` target Nau, which owns the primary display.
    """
    remaining: list[WindowOp] = []
    for op in ops:
        if op.op in ("suspend_hotkeys", "unsuspend_hotkeys", "notice",
                      "disable_all_topmost", "restore_all_topmost",
                      "open_rfb_tab",
                      "show_role", "hide_role", "activate_role",
                      "restack_primary"):
            remaining.append(op)
            continue

        if op.op == "send_key":
            hwnd = find_window_by_pid(nau_pid)
            if hwnd:
                send_key_to_window(hwnd, op.key)
            continue

        if op.op == "send_vk":
            hwnd = find_window_by_pid(nau_pid)
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
        "active_side": str(state.active_side),
        "portrait_filter": state.portrait_filter,
        "landscape_filter": state.landscape_filter,
        "volume": str(state.volume),
        "muted": "1" if state.muted else "0",
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    tmp.replace(state_file)


def _int_or(section, key: str, default: int) -> int:
    """An integer INI value, falling back to *default* when absent or malformed."""
    try:
        return int(section.get(key, default))
    except ValueError:
        return default


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
        active_side=_int_or(s, "active_side", 2),
        portrait_filter=s.get("portrait_filter", ""),
        landscape_filter=s.get("landscape_filter", ""),
        volume=_int_or(s, "volume", MAX_VOLUME),
        muted=s.get("muted", "0") == "1",
    )


def detect_sleep_gap(prev_wall: float, now_wall: float, *, threshold_s: float = 90.0) -> float | None:
    """Elapsed seconds if the loop stalled far longer than its tick cadence.

    The dispatch thread freezes while Windows is asleep or in modern standby;
    on resume the wall clock has jumped forward by the sleep duration.  A gap
    far above the ~50 ms tick interval means we just woke — the moment AHK's
    hotkeys are prone to not firing until the bridge is restarted.  Returns
    None for ordinary iterations (and for merely slow ticks, e.g. a stuck VLC
    HTTP call, which the threshold clears).
    """
    gap = now_wall - prev_wall
    return gap if gap >= threshold_s else None


class DispatchLoopRunner:
    """Runs dashboard polling and genau sync in-process."""

    def __init__(
        self,
        *,
        config: BridgeConfig,
        dashboard_cmd_file: Path,
        shared_state_file: Path,
        ahk_cmd_file: Path,
        nau_pid: int,
        portrait_pid: int = 0,
        landscape_pid: int = 0,
        dashboard_pid: int = 0,
        dashboard_enabled: bool,
        rfb_hwnd: int = 0,
        rfb_shortcut_target: str = "",
        rfb_shortcut_work_dir: str = "",
        rfb_shortcut_args: str = "",
        sync_interval_ms: int = 200,
        role_hwnds: dict[str, int] | None = None,
    ) -> None:
        self.config = config
        self.dashboard_cmd_file = dashboard_cmd_file
        self.shared_state_file = shared_state_file
        self.ahk_cmd_file = ahk_cmd_file
        self.nau_pid = nau_pid
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
        # Seeded from the startup sequencer, which resolved every window
        # while it was still visible — startup then hides the inactive
        # primary-slot windows, and hidden windows are invisible to the
        # pid/title lookups.
        self._role_hwnds: dict[str, int] = dict(role_hwnds or {})
        self._minimized_hwnds: list[int] = []
        # RFB tabs opened by locks are buffered and opened in one Chrome launch
        # per poll batch: "lock both" locks two videos in one tick, and two
        # rapid chrome.exe launches race Chrome's singleton and drop a tab.
        self._pending_rfb_urls: list[str] = []
        self._batching_rfb = False
        self.voice_controller: VoiceController | None = None
        # Each player's current video is sampled periodically and fed to watch
        # tracking ("breeding"), which classifies playback into completions/skips
        # for the stats file.  The satellites (2, 3) are polled over VLC's HTTP
        # interface and additionally feed a timeline, which lets a spoken command
        # be back-dated to the video on screen when the user started talking (see
        # _back_dated_video); the primary Nau player (1) is read from its status
        # file and needs no such timeline.
        self._watch_trackers: dict[int, WatchTracker] = {
            1: WatchTracker(),
            2: WatchTracker(),
            3: WatchTracker(),
        }
        self._timelines: dict[int, VideoTimeline] = {2: VideoTimeline(), 3: VideoTimeline()}
        self._satellite_ports = {2: config.portrait_port, 3: config.landscape_port}
        self._watch_stats_file = watch_stats_path(config.state_dir)
        self._last_watch_sample = 0.0
        # Hybrid funscript handoff: whether the funscript is driving the OSR2
        # right now (so Genau is paused and Nau's T-Code is on) or Genau is (a
        # funscript gap or an unscripted video).  None means "no decision applied
        # yet" — set outside hybrid so re-entry re-asserts the correct driver.
        self._hybrid_funscript_driving: bool | None = None

    _HOTKEY_TO_BUTTON: dict[str, str] = {}

    # Twice a second: the shared cadence for sampling every player's playback
    # (both satellites and the primary Nau feed).  A satellite video switch is
    # only ever bracketed by two samples, so this also bounds how far a back-dated
    # command can misplace a switch (the timeline halves it again by dating the
    # switch to the bracket's midpoint).
    _WATCH_SAMPLE_INTERVAL_S = 0.5

    # Commands that count as the user navigating away from a video — the signal
    # that classifies an early departure as a skip rather than a neutral advance.
    # The primary (Nau) navigates with next/prev only; it has no lock/weird/cycle.
    _WATCH_NAV_COMMANDS: dict[int, frozenset[str]] = {
        1: frozenset({"primary_prev", "primary_next"}),
        2: frozenset({"portrait_prev", "portrait_next", "portrait_cycle_action", "portrait_cycle_seed"}),
        3: frozenset({"landscape_prev", "landscape_next", "landscape_cycle_action", "landscape_cycle_seed"}),
    }

    _WATCH_DISCARD_COMMANDS: dict[str, int] = {"portrait_trash": 2, "landscape_trash": 3}

    def tick(self) -> None:
        """Run one iteration: poll dashboard, maybe sync genau."""
        # Sync state from shared file — AHK hotkey dispatches update it directly.
        shared = read_shared_state(self.shared_state_file)
        if shared is not None:
            self.state = shared

        # Hand the OSR2 to the current video's funscript (or back to Genau).
        # Runs before the command loop so a mode switch that also writes
        # genau_cmd (RESUME + HUD_ON on entering hybrid) is never clobbered by
        # the handoff in the same tick — the handoff instead lands next tick,
        # once that entry is on the current, now-hybrid mode.
        self._sync_hybrid_driver()

        # Dashboard commands (may be multiple if queued by rapid hotkey
        # presses).  Each raw line yields a command plus, for a spoken one, when
        # the utterance began; the command is then bound to concrete side
        # command(s): a side-agnostic "active_*" command (voice "lock", "next",
        # ...) resolves to whichever satellite was most recently addressed — by
        # voice or by keyboard nav — and a "both_*" command expands into its
        # Portrait + Landscape pair.
        # Buffer RFB opens across the whole batch so a "both" lock's two tabs
        # open in one Chrome launch (see _flush_rfb_tabs).
        self._batching_rfb = True
        try:
            for line in poll_dashboard_commands(self.dashboard_cmd_file):
                raw_command, spoken_at = parse_command_line(line)
                resolved = resolve_active_side_command(raw_command, self.state.active_side)
                for command in expand_both_command(resolved):
                    self._handle_command(command, spoken_at)
        finally:
            self._batching_rfb = False
        self._flush_rfb_tabs()

        self._sync_voice_suspension()

        # Periodic sync: z-order enforcement and dashboard update
        now = time.monotonic()
        if now - self._last_sync >= self.sync_interval_s:
            self._last_sync = now
            if self.dashboard_enabled:
                self._update_dashboard()
        if not self.state.omni_paused and now - self._last_watch_sample >= self._WATCH_SAMPLE_INTERVAL_S:
            self._last_watch_sample = now
            self._sample_satellites(now=now)
            self._sample_primary()

    def _sync_voice_suspension(self) -> None:
        """Freeze voice while omnipause holds, as AHK's ``Suspend`` freezes the keys.

        The suspend_hotkeys WindowOp only reaches AHK; voice lives in this
        process, so it is driven off ``omni_paused`` itself — the one authority
        both the dashboard and the shared state file agree on.  Suspended, only
        the exempt commands (resume, quit) still write, mirroring the AHK
        script's ``#SuspendExempt`` block.
        """
        if self.voice_controller is None:
            return
        if self.state.omni_paused:
            self.voice_controller.suspend()
        else:
            self.voice_controller.unsuspend()

    def _sync_hybrid_driver(self) -> None:
        """In hybrid, route the OSR2 to the funscript or Genau, moment to moment.

        Genau and a funscript both feed the broker's one UDP T-Code inlet, so
        only one may drive at a time.  The funscript drives while it is actively
        scripting (``has_funscript`` and not ``funscript_resting``); Genau drives
        the unscripted stretches — a video without a funscript, or a funscript's
        quiet lead-in and interior gaps.  Each handoff sets both levers: Nau's
        T-Code on + Genau paused for the funscript, or Nau's T-Code off (so its
        gap drift can't fight) + Genau resumed for Genau.  It is edge-triggered,
        so it fires once per handoff, not every tick.  Outside hybrid (or under
        omnipause) the remembered state is cleared so re-entry re-asserts the
        driver; leaving hybrid re-enables Nau's T-Code via the mode switch.
        """
        if self.state.primary_mode != "hybrid" or self.state.omni_paused:
            self._hybrid_funscript_driving = None
            return
        status = read_nau_status(self.config.nau_status_file)
        funscript_driving = status.funscript_driving
        if funscript_driving == self._hybrid_funscript_driving:
            return
        self._hybrid_funscript_driving = funscript_driving
        self.config.nau_cmd_file.write_text(
            "SET_TCODE_ENABLED 1" if funscript_driving else "SET_TCODE_ENABLED 0",
            encoding="utf-8",
        )
        self.config.genau_cmd_file.write_text(
            "PAUSE" if funscript_driving else "RESUME", encoding="utf-8"
        )

    def _handle_command(self, cmd: str, spoken_at: float | None = None) -> None:
        """Route one polled command (already expanded from any ``both_*``).

        ``spoken_at`` is when a voice command's utterance began, and None for
        the instantaneous hotkey and dashboard presses.
        """
        button = self._HOTKEY_TO_BUTTON.get(cmd, cmd)
        self._send_press(button)
        if cmd == "quit":
            self.ahk_cmd_file.write_text("exit", encoding="utf-8")
            return
        if cmd in ("help_reference", "help_reference_close"):
            # Pure dashboard-UI action: the press above tells the dashboard to
            # toggle/close the hotkeys/voice popup — nothing to dispatch here.
            return
        if cmd == "omniminimize":
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
                self._dispatch("quarter_button", spoken_at)
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
                self._dispatch("portrait_lock", spoken_at)
        elif cmd == "landscape_lock_on":
            if not self.state.locked3:
                self._dispatch("landscape_lock", spoken_at)
        elif cmd == "portrait_lock_off":
            if self.state.locked2:
                self._dispatch("portrait_lock", spoken_at)
        elif cmd == "landscape_lock_off":
            if self.state.locked3:
                self._dispatch("landscape_lock", spoken_at)
        elif cmd == "fmode_on":
            if not self.state.f_mode_enabled:
                self._dispatch("fmode_toggle", spoken_at)
        elif cmd == "fmode_off":
            if self.state.f_mode_enabled:
                self._dispatch("fmode_toggle", spoken_at)
        elif cmd == "broker_start":
            self._handle_broker_start()
        elif cmd == "broker_stop":
            self._handle_broker_stop()
        elif cmd in ("voice_off", "voice_toggle"):
            self._handle_voice_toggle(cmd)
        else:
            self._dispatch(cmd, spoken_at)

    def _sample_satellites(self, *, now: float) -> None:
        """Sample each satellite's current video for the trackers and timelines."""
        for which, port in self._satellite_ports.items():
            fraction = get_playback_fraction(port, self.config.vlc_password)
            if fraction is None:
                continue
            path = get_current_file_path(port, self.config.vlc_password)
            self._timelines[which].observe(path, now=now)
            for event, video in self._watch_trackers[which].observe(path, fraction):
                record_watch_event(self._watch_stats_file, video, event)

    def _sample_primary(self) -> None:
        """Sample the primary Nau player's current video for watch tracking.

        Nau publishes its playback to the status file; the watched fraction is
        position/duration.  A paused player, one with nothing loaded, or one
        whose duration is not yet known yields no usable sample, so those ticks
        are dropped rather than fed to the tracker.
        """
        status = read_nau_status(self.config.nau_status_file)
        if not status.video or status.paused or status.duration_ms <= 0:
            return
        fraction = status.position_ms / status.duration_ms
        for event, video in self._watch_trackers[1].observe(status.video, fraction):
            record_watch_event(self._watch_stats_file, video, event)

    def _back_dated_video(self, command: str, spoken_at: float | None) -> str:
        """The video *command* was aimed at, or "" for "whatever is playing now".

        A phrase is only recognized once the speaker stops, so a satellite can
        have auto-advanced between "lock…" and "…portrait".  The satellite's
        timeline says which video was on screen when the utterance began — the
        one the speaker was looking at, and therefore meant.  Hotkeys are
        instantaneous and name no video.
        """
        if spoken_at is None:
            return ""
        timeline = self._timelines.get(command_side(command))
        if timeline is None:
            return ""
        return timeline.path_at(spoken_at)

    def _dispatch(self, command: str, spoken_at: float | None = None) -> None:
        logger.info("Dispatching command: %s", command)
        for which, nav_commands in self._WATCH_NAV_COMMANDS.items():
            if command in nav_commands:
                self._watch_trackers[which].note_user_nav()
        discard_which = self._WATCH_DISCARD_COMMANDS.get(command)
        if discard_which is not None:
            self._watch_trackers[discard_which].note_discard()
        new_state, ops = dispatch_command(
            command, self.state, self.config,
            target_path=self._back_dated_video(command, spoken_at),
        )
        self.state = new_state
        remaining = execute_window_ops(ops, self.nau_pid)
        suppress_unsuspend = os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
        for op in remaining:
            if op.op == "show_role":
                # Restore (un-minimize) rather than SW_SHOW: the idle primary
                # player is parked by minimizing it (keeps its taskbar button),
                # so bringing it back is a restore.  No-activate — activate_role
                # handles focus — and DWM transitions are disabled, so it's
                # instant.
                hwnd = self._resolve_role(op.key)
                if hwnd:
                    restore_window(hwnd, activate=False)
                continue
            if op.op == "hide_role":
                # Minimize instead of SW_HIDE so the window keeps its taskbar
                # button (running indicator) the whole session.
                hwnd = self._resolve_role(op.key)
                if hwnd:
                    minimize_window(hwnd, activate=False)
                continue
            if op.op == "activate_role":
                if os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1":
                    hwnd = self._resolve_role(op.key)
                    if hwnd:
                        activate_window(hwnd)
                continue
            if op.op == "restack_primary":
                # Re-stack the overlapping Nau/Genau pair for the current mode.
                # Not integration-guarded: SetWindowPos(HWND_TOPMOST) uses
                # SWP_NOACTIVATE, so it changes only the z-band, never focus.
                self._restack_primary_slot()
                continue
            if op.op == "disable_all_topmost":
                self._remove_all_topmost()
                continue
            if op.op == "restore_all_topmost":
                self._restore_all_topmost()
                continue
            if suppress_unsuspend and op.op == "unsuspend_hotkeys":
                continue
            if op.op == "open_rfb_tab":
                self._pending_rfb_urls.append(op.key)
                continue
            if op.op == "notice":
                notice(logger, op.key, source=op.source, level=op.level)
            else:
                self.ahk_cmd_file.write_text(op.op, encoding="utf-8")
        write_shared_state(self.shared_state_file, self.state)
        # Outside a poll batch (e.g. a lone lock) there is nothing to coalesce
        # with, so open immediately; within a batch the tick flushes once.
        if not self._batching_rfb:
            self._flush_rfb_tabs()
        if self.dashboard_enabled:
            self._update_dashboard()

    def _flush_rfb_tabs(self) -> None:
        """Open every buffered RFB URL as tabs in one Chrome launch."""
        urls = self._pending_rfb_urls
        self._pending_rfb_urls = []
        if not urls:
            return
        if self.rfb_hwnd and self.rfb_shortcut_target:
            open_rfb_tab(
                urls=urls,
                shortcut_target=self.rfb_shortcut_target,
                shortcut_work_dir=self.rfb_shortcut_work_dir,
                shortcut_args=self.rfb_shortcut_args,
            )
            logger.info("Opened RFB tab(s): %s", ", ".join(urls))

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
                primary_mode=self.state.primary_mode,
                portrait_locked=self.state.locked2,
                landscape_locked=self.state.locked3,
                omni_paused=self.state.omni_paused,
                voice_active=voice_active,
            )
        except Exception:
            pass

    def _resolve_role(self, role: str) -> int:
        """HWND for a managed window role, cached on first sight.

        Hidden windows are invisible to the pid/title lookups, so a
        window's HWND must be captured while it is visible (startup shows
        everything) and reused to show it again later.
        """
        hwnd = self._role_hwnds.get(role, 0)
        if hwnd:
            return hwnd
        if role == "genau":
            hwnd = find_window_by_title("Genau")
        elif role == "nau":
            # The venv pythonw launcher's PID differs from the interpreter
            # that owns the SDL window, so fall back to the exact window
            # title (exact: "Nau" is a substring of "Genau").
            hwnd = find_window_by_pid(self.nau_pid) or find_window_by_title("Nau", exact=True)
        elif role == "portrait":
            hwnd = find_window_by_pid(self.portrait_pid)
        elif role == "landscape":
            hwnd = find_window_by_pid(self.landscape_pid)
        elif role == "dashboard":
            hwnd = self._find_dashboard_hwnd()
        elif role == "logs":
            # The dashboard process owns it, so it shares the dashboard's pid
            # ambiguity; the exact title is what reliably resolves it.
            hwnd = find_window_by_title(LOG_PANEL_WINDOW_TITLE, exact=True)
        elif role == "rfb":
            hwnd = self.rfb_hwnd
        if hwnd:
            self._role_hwnds[role] = hwnd
        return hwnd

    def _visible_roles(self) -> list[str]:
        """Roles whose windows the current mode keeps on screen."""
        slot = {
            "genau": ["genau"],
            "hybrid": ["nau", "genau"],
        }.get(self.state.primary_mode, ["nau"])
        return ["rfb", "portrait", "landscape", "dashboard", "logs", *slot]

    def _remove_all_topmost(self) -> None:
        """Drop EVERY managed window out of the TOPMOST band (omnipause frees
        the desktop).  Dropping unconditionally — not just the normally-topmost
        roles — is what stops Nau from being stranded on top in nau mode, where
        it does carry the topmost flag."""
        for role in MANAGED_ROLES:
            hwnd = self._resolve_role(role)
            if hwnd:
                set_always_on_top(hwnd, False)

    def _restore_all_topmost(self) -> None:
        """Re-apply the topmost bands for the current mode after omnipause.

        The fixed windows (own rects) go straight back to topmost; the
        overlapping Nau/Genau pair is re-stacked so Genau's HUD sits above Nau's
        video in hybrid.  See :meth:`_restack_primary_slot`.
        """
        for role in FIXED_TOPMOST_ROLES:
            hwnd = self._resolve_role(role)
            if hwnd:
                set_always_on_top(hwnd, True)
        self._restack_primary_slot()

    def _restack_primary_slot(self) -> None:
        """Re-establish the Nau/Genau z-order for the current mode.

        Nau and Genau share one screen rect — in hybrid Genau's transparent HUD
        overlays Nau's video — so unlike every other window they OVERLAP and need
        explicit stacking.  Demote both, then promote bottom-to-top so the last
        promotion lands highest:

          * nau mode   — promote Nau (Genau hidden).
          * hybrid     — promote Nau, then Genau ABOVE it, so the HUD overlays
                         the video and both float above the desktop.
          * genau mode — promote Genau (Nau hidden).

        Promoting Nau before Genau is what keeps the HUD over the video — the
        demote-then-promote-in-order technique the old z_order module used.
        """
        mode = self.state.primary_mode
        nau = self._resolve_role("nau")
        genau = self._resolve_role("genau")
        for hwnd in (nau, genau):
            if hwnd:
                set_always_on_top(hwnd, False)
        if nau and role_topmost("nau", mode):
            set_always_on_top(nau, True)
        if genau and role_topmost("genau", mode):
            set_always_on_top(genau, True)

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
            hwnd = find_window_by_title("Fun Time", exact=True)
            if hwnd:
                logger.info(
                    "Dashboard found by title (hwnd=%d) but NOT by pid %d",
                    hwnd, self.dashboard_pid,
                )
        return hwnd

    def _handle_omniminimize(self) -> None:
        """Minimize the windows the current mode shows — the "omniminimize" command.

        Only mode-visible windows are minimized (SW_MINIMIZE would drag a
        hidden slot-mate back into view), each with ``activate=False`` so
        minimizing one never yanks focus to the next.  The minimized set is
        remembered so omnirestore brings back exactly these windows.
        """
        self._minimized_hwnds = []
        for role in self._visible_roles():
            hwnd = self._resolve_role(role)
            if hwnd:
                minimize_window(hwnd, activate=False)
                self._minimized_hwnds.append(hwnd)

    def _handle_omnirestore(self) -> None:
        """Un-minimize exactly the windows omniminimize minimized."""
        for hwnd in self._minimized_hwnds:
            restore_window(hwnd, activate=False)
        self._minimized_hwnds = []

    def _log_topmost_state(self, label: str) -> None:
        """Log every managed window's resolved hwnd and topmost state.

        Entering omnipause should leave EVERY window non-topmost; a window still
        topmost at "post-enter" is one the drop didn't reach (an unresolved or
        re-asserting window).  Leaving restores the per-mode bands.  This is the
        diagnostic that pins which window (e.g. a satellite VLC) misbehaves.
        """
        parts = []
        for role in MANAGED_ROLES:
            hwnd = self._resolve_role(role)
            state = is_window_topmost(hwnd) if hwnd else "n/a"
            parts.append(f"{role}={hwnd}:{state}")
        logger.info("Topmost [%s] mode=%s: %s", label, self.state.primary_mode, "  ".join(parts))

    def _handle_omnipause_toggle(self) -> None:
        """Toggle omnipause with topmost management for all windows.

        Topmost removal (enter) and restoration (leave) are driven by
        the disable_all_topmost / restore_all_topmost WindowOps that
        command_dispatch emits — _dispatch handles them automatically.
        """
        was_paused = self.state.omni_paused
        self._dispatch("omnipause_toggle")
        self._log_topmost_state("post-leave" if was_paused else "post-enter")

    def _handle_enter_omnipause(self) -> None:
        """Enter omnipause with topmost management (Space key — enter only, no leave).

        Topmost removal is driven by the disable_all_topmost WindowOp
        that command_dispatch emits — _dispatch handles it automatically.
        """
        self._dispatch("enter_omnipause")
        self._log_topmost_state("post-enter")

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
            owner_hwnd = self._resolve_role("nau")
            selected = show_open_file_dialog(default_dir or "", owner_hwnd=owner_hwnd)
            if selected:
                # Nau owns the primary display; play the pick there, paired with
                # its funscript when one exists at the mirrored path.
                mirrored = build_mirrored_funscript_path(selected)
                if mirrored and Path(mirrored).exists():
                    command = f"PLAY_FILE {selected}\t{mirrored}"
                else:
                    command = f"PLAY_FILE {selected}"
                self.config.nau_cmd_file.write_text(command, encoding="utf-8")
        finally:
            if should_manage_omnipause:
                self._dispatch("leave_omnipause")

    def run(self) -> None:
        """Main loop — call from a background thread."""
        # Log the topmost state once the loop starts so a "windows not all on top
        # after launch" report can be pinned to the exact window that missed its
        # startup promotion.
        self._log_topmost_state("startup")
        last_wall = time.time()
        while not self._stop.is_set():
            now = time.time()
            gap = detect_sleep_gap(last_wall, now)
            if gap is not None:
                logger.warning(
                    "Dispatch loop resumed after %.0fs stall — likely system "
                    "sleep/standby; AHK hotkeys may be dead until Fun Time is restarted",
                    gap,
                )
            last_wall = now
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
        portrait_port=int(manifest["vlc"]["vlc2_port"]),
        landscape_port=int(manifest["vlc"]["vlc3_port"]),
        vlc_password=manifest["vlc"]["vlc_pass"],
        favs_file=Path(manifest["media"]["favs_file"]),
        weird_dir=Path(manifest["media"]["weird_dir"]),
        state_dir=Path(manifest["commands"]["dashboard_state_file"]).parent,
        primary_sources=manifest["media"]["nau_library_sources"],
        portrait_sources=manifest["media"]["portrait_dirs"],
        landscape_sources=manifest["media"]["landscape_dirs"],
        genau_mode_file=Path(manifest["commands"]["genau_mode_file"]),
        genau_cmd_file=Path(manifest["commands"]["genau_cmd_file"]),
        genau_paused_file=Path(manifest["commands"]["genau_paused_file"]),
        audio_paused_file=Path(manifest["commands"]["audio_paused_file"]),
        audio_volume_file=Path(manifest["commands"]["audio_volume_file"]),
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
