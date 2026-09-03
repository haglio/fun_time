"""Background dispatch loop for dashboard commands and genau sync.

Runs in a thread alongside the AHK hotkey script, handling periodic
dispatch directly in Python instead of spawning subprocesses.
"""
from __future__ import annotations

import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from player_core.file_channel import append_command

from .bridge_records import FAILED_NOTICE_LEVEL, BridgeConfig, Op, WindowOp
from .clipper_save import save_clip_session
from .command_dispatch import dispatch_command, routes_to_origenerator
from .dashboard_actions import HELP_REFERENCE_COMMANDS
from .event_log import FAVORITE, NOTICE, SOURCE_MAIN, notice
from .hud_feed import HudFeed
from .hybrid_driver import HybridDriver
from .hud_transport import HudPublisher
from .library_browser import browse_library
from .manifest import WINDOWS_BRIDGE_MANIFEST_FILENAME, LaunchManifest
from .mode_plan import genau_active
from .modes import build_mirrored_funscript_path
from .satellites_mode import origenerator_shows
from .shared_state import BridgeState, read_shared_state, write_shared_state
from .voice_commands import parse_command_line
from .watch_sampling import WatchSampler
from .watch_stats import watch_stats_path
from .windows_bridge_random_favs_browser import ChromeShortcut, open_rfb_tab
from .voice_control import SUSPEND_EXEMPT_COMMANDS, VoiceController
from .dashboard_bridge import write_dashboard_snapshot
from .player_status import is_broker_heartbeat_fresh
from .role_windows import WindowRoles
from .windows_bridge_startup import launch_broker_tray, stop_broker_processes
from .window_roles import visible_roles
from .win32 import force_foreground_window, window_exists, window_rect

logger = logging.getLogger(__name__)


# What Nau's own notice levels mean here.  Nau has no palette — it names the kind
# of thing that happened and this side picks the color, the same way the ops
# raised in :mod:`fun_time.command_dispatch` do.
_NAU_NOTICE_LEVELS = {"error": FAILED_NOTICE_LEVEL, "favorite": FAVORITE}



def read_nau_notice(path) -> tuple[float, str, str]:
    """Nau's latest one-shot notice as (sequence, level, message).

    Nau bumps the sequence whenever it raises one; (0, "", "") means there is
    nothing to read. Only Nau knows whether a clip jump had a target, so this is
    how "full video not available" reaches the overlay.
    """
    try:
        values = dict(
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        return float(values.get("seq", "0")), values.get("level", "notice"), values.get("message", "")
    except (OSError, ValueError):
        return 0, "", ""


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


# The side-agnostic actions the main player (Nau) answers, and what it answers with.
# Navigation is the same gesture on every player; "end loop" is the same *word* for
# a different loop — Nau's A-B loop rather than a satellite's group loop.  A lock
# is the same thing on all three — repeat-one on what is on screen — so the bare
# word reaches whichever was last addressed, the main player included.  So is F-mode,
# though what it narrows to differs: the favorites on a satellite, the videos
# with a funscript on the main player.
_MAIN_EQUIVALENTS = {
    "next": "main_next",
    "prev": "main_prev",
    "no_loop": "nau_loop_cancel",
    "lock_on": "main_lock_on",
    "lock_off": "main_lock_off",
    "fmode": "main_fmode",
    "fmode_on": "main_fmode_on",
    "fmode_off": "main_fmode_off",
    # And so is a reset — "drop whatever is narrowing this player", which on the
    # main player is its length mode and its F-mode where on a satellite it is the
    # act filter and the loop.
    "reset": "main_reset",
    # So are the two browse orders: every player browses newest-first or
    # shuffled, and the main player is no exception now that Genau answers them too.
    "latest": "main_latest",
    "shuffle": "main_shuffle",
}


def resolve_active_side_command(command: str, active_side: int) -> str:
    """Rewrite a side-agnostic ``active_*`` command onto the active player.

    ``active_next``/``active_prev`` follow the last player navigated — main
    (Nau, slot 1), portrait (2), or landscape (3).  ``active_lock_on``/``_off``
    reach the main player too, meaning there what they mean on a satellite: hold the
    video on screen, or let the playlist walk on.  So does ``active_no_loop``, but
    meaning the loop *it* has: Nau's A-B loop, where on a satellite the same phrase
    ends a group loop.  The rest (weird, cycle) exist only on the satellites, so
    while the main player is active they resolve to nothing — returned unchanged, which
    is a no-op downstream.  Every non-``active_`` command passes through unchanged.
    """
    if not command.startswith("active_"):
        return command
    action = command[len("active_"):]
    if active_side == 1:
        # What each side-agnostic action means on the main player; anything absent
        # here simply has no main-player equivalent.
        return _MAIN_EQUIVALENTS.get(action, command)
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


def detect_sleep_gap(prev_wall: float, now_wall: float, *, threshold_s: float = 90.0) -> float | None:
    """Elapsed seconds if the loop stalled far longer than its tick cadence.

    The dispatch thread freezes while Windows is asleep or in modern standby;
    on resume the wall clock has jumped forward by the sleep duration.  A gap
    far above the ~50 ms tick interval means we just woke — the moment AHK's
    hotkeys are prone to not firing until the bridge is restarted.  Returns
    None for ordinary iterations (and for merely slow ticks, e.g. a blocking
    file read against a stalled disk, which the threshold clears).
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
        windows: WindowRoles,
        dashboard_enabled: bool,
        manifest_path: Path | None = None,
        hud_publisher: HudPublisher | None = None,
        rfb_shortcut: ChromeShortcut | None = None,
        sync_interval_ms: int = 200,
    ) -> None:
        self.config = config
        self.dashboard_cmd_file = dashboard_cmd_file
        self.shared_state_file = shared_state_file
        self.ahk_cmd_file = ahk_cmd_file
        # The session's launch manifest — handed to the library browser, which
        # reads the same file every other child process does, so the browse can
        # never disagree with the session about what the library is.
        self.manifest_path = manifest_path or (
            config.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        # Every window the session manages, and the one cache of their HWNDs:
        # the tick and the library browser's own thread both go through here.
        self.windows = windows
        self.dashboard_enabled = dashboard_enabled
        # This loop holds the state each player's own HUD is drawn from (locks,
        # filters, loops) and already ticks, so it is what feeds them.
        self.hud = HudFeed(config=config, publisher=hud_publisher)
        self.rfb_shortcut = rfb_shortcut
        self.sync_interval_s = sync_interval_ms / 1000
        self.state = BridgeState()
        self._last_sync = 0.0
        self._last_dashboard_warning = float("-inf")
        self._stop = threading.Event()
        self._browse_lock = threading.Lock()
        # The browse now on screen, if any.  It is launched mid-session, so it
        # cannot join the startup children the teardown list kills; this holds it
        # instead, and stop() is where quitting takes it with the session.
        self._browser_process: subprocess.Popen | None = None
        self._press_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._press_port: int | None = None
        self._press_port_file = config.state_dir / "dashboard_press_port.txt"
        # RFB tabs opened by locks are buffered and opened in one Chrome launch
        # per poll batch: "lock both" locks two videos in one tick, and two
        # rapid chrome.exe launches race Chrome's singleton and drop a tab.
        self._pending_rfb_urls: list[str] = []
        self._batching_rfb = False
        # Latch whatever is already on disk, so a notice left over from a
        # previous session does not flash the moment this one opens.
        self._last_nau_notice_seq = read_nau_notice(
            getattr(config, "nau_notice_file", None) or Path("nau_notice.txt")
        )[0]
        self.voice_controller: VoiceController | None = None
        # Watch tracking ("breeding"): every player's current clip, sampled and
        # classified into completions and skips for the stats file.
        self.watch = WatchSampler(
            nau_status_file=config.nau_status_file,
            satellite_status_files={2: config.portrait_status_file,
                                    3: config.landscape_status_file},
            stats_file=watch_stats_path(config.state_dir),
        )
        # Genau and a funscript both feed the broker's one T-Code inlet, so in
        # hybrid something has to hand the device between them.
        self.hybrid = HybridDriver(
            nau_status_file=config.nau_status_file,
            nau_cmd_file=config.nau_cmd_file,
            genau_cmd_file=config.genau_cmd_file,
        )

    def tick(self) -> None:
        """Run one iteration: poll dashboard, maybe sync genau."""
        self._flash_nau_notice()

        # Sync state from shared file — AHK hotkey dispatches update it directly.
        shared = read_shared_state(self.shared_state_file)
        if shared is not None:
            self.state = shared

        # Hand the OSR2 to the current video's funscript (or back to Genau).
        # Runs before the command loop so a mode switch that also writes
        # genau_cmd (RESUME + HUD_ON on entering hybrid) is never clobbered by
        # the handoff in the same tick — the handoff instead lands next tick,
        # once that entry is on the current, now-hybrid mode.
        self.hybrid.sync(self.state.main_mode, paused=self.state.omni_paused)

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
        # After the batch, so a switch and a switch straight back inside one
        # batch cancel rather than minimize the player they just brought back.
        self.windows.flush_pending_hides()

        self._sync_voice_suspension()

        # Periodic sync: z-order enforcement and dashboard update
        now = time.monotonic()
        if now - self._last_sync >= self.sync_interval_s:
            self._last_sync = now
            self._converge_origenerator_window()
            if self.dashboard_enabled:
                self._update_dashboard()
        self.watch.sample_due(now=now, paused=self.state.omni_paused)
        self.hud.publish_due(self.state, now=now)

    def _sync_voice_suspension(self) -> None:
        """Freeze voice while omnipause holds, as AHK's ``Suspend`` freezes the keys.

        The suspend_hotkeys WindowOp only reaches AHK; voice lives in this
        process, so it is driven off ``omni_paused`` itself — the one authority
        both the dashboard and the shared state file agree on.  Suspended, only
        the exempt commands (resume, quit, relief) still write, mirroring the AHK
        script's ``#SuspendExempt`` block.
        """
        if self.voice_controller is None:
            return
        if self.state.omni_paused:
            self.voice_controller.suspend()
        else:
            self.voice_controller.unsuspend()

    def _flash_nau_notice(self) -> None:
        """Surface anything Nau has raised since the last tick, once.

        Nau names the kind rather than the color: "error" for a request with
        nowhere to go, "favorite" for one about a funscript — which is what green
        is kept for here — and anything else is an ordinary white notice.
        """
        path = getattr(self.config, "nau_notice_file", None)
        if path is None:
            return
        seq, level, message = read_nau_notice(path)
        if seq <= self._last_nau_notice_seq:
            return
        self._last_nau_notice_seq = seq
        if message:
            notice(
                logger, message, source="main",
                level=_NAU_NOTICE_LEVELS.get(level, NOTICE),
            )

    def _handle_command(self, cmd: str, spoken_at: float | None = None) -> None:
        """Route one polled command (already expanded from any ``both_*``).

        ``spoken_at`` is when a voice command's utterance began, and None for
        the instantaneous hotkey and dashboard presses.
        """
        if (
            self.state.omni_paused
            and spoken_at is not None
            and cmd not in SUSPEND_EXEMPT_COMMANDS
        ):
            # Freeze SPOKEN commands while paused — a mis-heard phrase must not
            # act on a paused room.  ``spoken_at`` marks a voice line; the
            # deliberate mouse (dashboard, lock HUD) stays live because a click
            # is not an accident.  This backstops VoiceController's own suspend,
            # closing the entry race where a phrase is written in the tick before
            # the suspend flag is set.  Exempts the same resume/quit/relief
            # voice does.
            logger.debug("OmniPause dropped spoken command: %s", cmd)
            return
        self._send_press(cmd)
        if cmd == "quit":
            self.ahk_cmd_file.write_text("exit", encoding="utf-8")
            return
        if cmd in HELP_REFERENCE_COMMANDS:
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
                self._handle_enter_omnipause("enter_omnipause")
        elif cmd == "relief_omnipause":
            # No already-paused guard, unlike Space above: a session can be paused
            # with the device still on the user, which is the case relief exists
            # for, so the retract must go out even from inside omnipause.
            self._handle_enter_omnipause("relief_omnipause")
        elif cmd == "browse_library":
            threading.Thread(
                target=self._handle_browse_library,
                daemon=True,
                name="library-browser",
            ).start()
        elif cmd == "broker_panel":
            threading.Thread(
                target=self._handle_broker_toggle,
                daemon=True,
                name="broker-toggle",
            ).start()
        elif cmd == "backslash_key":
            if genau_active(self.state.main_mode):
                self._send_press("quarter_button")
                self._dispatch("quarter_button", spoken_at)
            else:
                self._send_press("browse_library")
                threading.Thread(
                    target=self._handle_browse_library,
                    daemon=True,
                    name="library-browser",
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
        elif cmd == "broker_start":
            self._handle_broker_start()
        elif cmd == "broker_stop":
            self._handle_broker_stop()
        elif cmd in ("voice_off", "voice_toggle"):
            self._handle_voice_toggle(cmd)
        else:
            self._dispatch(cmd, spoken_at)

    def _dispatch(self, command: str, spoken_at: float | None = None) -> None:
        logger.info("Dispatching command: %s", command)
        # A transport verb bound for an Origenerator show steps that show, not
        # the paused player underneath — booking it here would classify the
        # player's frozen clip as skipped or discarded when nobody touched it.
        if not routes_to_origenerator(command, self.state, self.config):
            self.watch.note_command(command)
        new_state, ops = dispatch_command(
            command, self.state, self.config,
            target_path=self.watch.video_at(command, spoken_at),
        )
        self.state = new_state
        for op in ops:
            handler = _OP_HANDLERS.get(op.op)
            if handler is None:
                # Never the AHK fall-through: an op the interpreter does not
                # know is a bug here, not a verb for the hotkey script.
                logger.error("unhandled window op %r", op.op)
                continue
            handler(self, op)
        write_shared_state(self.shared_state_file, self.state)
        # Outside a poll batch (e.g. a lone lock) there is nothing to coalesce
        # with, so open immediately; within a batch the tick flushes once.
        if not self._batching_rfb:
            self._flush_rfb_tabs()
        if self.dashboard_enabled:
            self._update_dashboard()

    def _flush_rfb_tabs(self) -> None:
        """Open every buffered RFB URL as tabs in the session's own Chrome window.

        The RFB runs in the user's own Chrome profile, so his personal windows
        are candidates for the tab too: Chrome's ``FindTabbedBrowser`` walks
        that profile's windows most-recently-active first, so the window he
        touched last would win and the lock's tab would land behind the
        players.  Activating the RFB window first is what settles it — it goes
        to the head of Chrome's own activation order, and Chrome shows the
        window it opens into either way, so this only decides which one rises.

        A dead handle means Fun Time has no window of its own left to open
        into, and every URL handed over then lands in one of his: worth losing
        the tab over, so the launch is skipped entirely.

        In origenerator mode the buffer holds instead of flushing: the RFB is
        under the hosted app's window, and opening a tab would force Chrome over
        it.  The locks queue, and switching back to player mode flushes them.
        """
        if origenerator_shows(self.state.satellites_mode):
            return
        urls = self._pending_rfb_urls
        self._pending_rfb_urls = []
        if not urls or self.rfb_shortcut is None or not self.rfb_shortcut.target:
            return
        if not window_exists(self.windows.rfb_hwnd):
            logger.warning(
                "RFB tab(s) skipped: no Random Favs Browser window to open into: %s",
                ", ".join(urls),
            )
            return
        if not force_foreground_window(self.windows.rfb_hwnd):
            # Not fatal, and expected on the integration suite's hidden desktop,
            # which has no foreground window to become.
            logger.info("RFB window did not take the foreground before the tab handoff")
        open_rfb_tab(urls=urls, shortcut=self.rfb_shortcut)
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
        except (OSError, ValueError) as exc:
            logger.debug("press hint for %r not sent: %s", action, exc)

    def _update_dashboard(self) -> None:
        """Write the dashboard's snapshot; only the disk may fail quietly.

        OSError (the state file held open by a reader) warns, throttled to once
        per failing minute since this runs twice a second; anything else is a
        bug of ours that the loop's per-tick exception log must show.
        """
        try:
            voice_active = self.voice_controller is not None and not self.voice_controller.is_muted
            write_dashboard_snapshot(
                str(self.config.dashboard_state_file),
                omni_paused=self.state.omni_paused,
                voice_active=voice_active,
            )
        except OSError as exc:
            now = time.monotonic()
            if now - self._last_dashboard_warning >= 60.0:
                self._last_dashboard_warning = now
                logger.warning("dashboard snapshot write failed: %s", exc)

    def _converge_origenerator_window(self) -> None:
        """Converge the hosted app's window — never during OmniPause, whose
        window state is its own."""
        if self.state.omni_paused:
            return
        self.windows.converge_origenerator_window(
            self.state.main_mode, self.state.satellites_mode)

    def _broker_heartbeat_is_fresh(self) -> bool:
        """Whether the broker is currently talking to the OSR2 — not whether it exists.

        osr2_broker writes the heartbeat only while it holds the serial port, so
        a stale one means "no broker reaching the device", which a live broker
        with the OSR2 switched off satisfies.  Reading this as "is the broker
        alive" is what had the start paths killing healthy brokers.
        """
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

    def _handle_clipper_save(self) -> None:
        """Run the clipper save and flash its toast — from the clipper-save thread."""
        message = save_clip_session(self.config)
        if message:
            notice(logger, message, source=SOURCE_MAIN)

    def _handle_broker_toggle(self) -> None:
        """Stop the broker if it is running, start it if it is not.

        Only the stopping half may kill: the heartbeat goes stale on a live
        broker whenever the OSR2 is off.
        """
        if self._broker_heartbeat_is_fresh():
            stop_broker_processes()
        else:
            launch_broker_tray(self.config.broker_tray_launcher)

    def _handle_broker_start(self) -> None:
        """Start the broker if the heartbeat says none is running.

        A start, never a restart: the heartbeat only ticks while the broker
        holds the serial port, so a broker that cannot reach a powered-off OSR2
        reads as dead while it is alive and serving every other client.
        """
        if not self._broker_heartbeat_is_fresh():
            threading.Thread(
                target=lambda: launch_broker_tray(self.config.broker_tray_launcher),
                daemon=True,
                name="broker-start",
            ).start()

    def _handle_broker_stop(self) -> None:
        """Stop broker only if currently running."""
        if self._broker_heartbeat_is_fresh():
            threading.Thread(
                target=stop_broker_processes,
                daemon=True,
                name="broker-stop",
            ).start()

    def _handle_omniminimize(self) -> None:
        """Minimize the windows the current mode shows — the "omniminimize" command.

        Only mode-visible windows are minimized (SW_MINIMIZE would drag a
        hidden slot-mate back into view), each with ``activate=False`` so
        minimizing one never yanks focus to the next.  The minimized set is
        remembered so omnirestore brings back exactly these windows.
        """
        self.windows.minimize_all(
            visible_roles(self.state.main_mode, self.state.satellites_mode))

    def _handle_omnirestore(self) -> None:
        """Un-minimize exactly the windows omniminimize minimized.

        That set is every window the mode had on screen, so it covers any that a
        HUD button had already parked — they are up again, and the parked list is
        dropped so a later resume does not "restore" windows already restored.
        """
        self.windows.restore_minimized()

    def _log_topmost_state(self, label: str) -> None:
        logger.info("Topmost [%s] mode=%s: %s", label, self.state.main_mode,
                    self.windows.topmost_report())

    def _handle_omnipause_toggle(self) -> None:
        """Toggle omnipause with topmost management for all windows.

        Topmost removal (enter) and restoration (leave) are driven by
        the disable_all_topmost / restore_all_topmost WindowOps that
        command_dispatch emits — _dispatch handles them automatically.
        """
        was_paused = self.state.omni_paused
        self._dispatch("omnipause_toggle")
        self._log_topmost_state("post-leave" if was_paused else "post-enter")

    def _handle_enter_omnipause(self, command: str) -> None:
        """Enter omnipause with topmost management — enter only, no leave.

        *command* is the way in: ``enter_omnipause`` (Space) parks the OSR2,
        ``relief_omnipause`` (Shift+Esc) retracts it instead.  Everything else
        about the entry is the same, topmost removal included — that is driven by
        the disable_all_topmost WindowOp command_dispatch emits, which _dispatch
        handles automatically.
        """
        self._dispatch(command)
        self._log_topmost_state("post-enter")

    def _handle_browse_library(self) -> None:
        """Browse the library and play the pick in Nau, one browse at a time.

        Serialized on a lock: the browser is the user's window, not the dispatch
        loop's, so a second request while one is open would stack a second
        browser and a second topmost drop/restore pair.
        """
        if not self._browse_lock.acquire(blocking=False):
            return
        try:
            self._browse_library_inner()
        finally:
            self._browse_lock.release()

    def _browse_library_inner(self) -> None:
        # Browsing keeps everything playing — it must NOT enter OmniPause (a
        # pause here once stranded the satellites and voice frozen).  All the
        # browser needs is to not be buried under the always-on-top windows, so
        # drop the topmost bands for its duration and restore them after —
        # playback and voice are never touched.  Under OmniPause the bands are
        # already down and must stay down (restoring them would strand windows
        # on top mid-pause), so only manage them when not paused.
        #
        # The hotkeys go the same way, for the browser's sake: they are global
        # and they *consume* the press, so the arrows would move the portrait
        # satellite instead of the selection.  Suspending hands the keyboard to
        # the browser; under OmniPause they are already suspended and the pause
        # owns that hold, so it is left to release it.
        manage_session = not self.state.omni_paused

        if manage_session:
            self.windows.remove_all_topmost()
            self.ahk_cmd_file.write_text("suspend_hotkeys", encoding="utf-8")

        try:
            # Over Nau's own rect: the pick plays there, so the browse stands
            # where the video will, and covers nothing else on either monitor.
            nau_hwnd = self.windows.hwnd("nau")
            selected = browse_library(
                self.manifest_path,
                self.config.python_exe,
                over=window_rect(nau_hwnd) if nau_hwnd else None,
                runner=self._run_browser,
            )
            if selected:
                # Nau owns the main player; play the pick there, paired with
                # its funscript when one exists at the mirrored path.
                mirrored = build_mirrored_funscript_path(selected)
                if mirrored and Path(mirrored).exists():
                    command = f"PLAY_FILE {selected}\t{mirrored}"
                else:
                    command = f"PLAY_FILE {selected}"
                append_command(self.config.nau_cmd_file, command)
        finally:
            if manage_session:
                self.windows.restore_all_topmost(
            self.state.main_mode, self.state.satellites_mode)
                self.ahk_cmd_file.write_text("unsuspend_hotkeys", encoding="utf-8")

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

    def _run_browser(self, command, **kwargs) -> None:
        """Run the browser, holding it while it is up so :meth:`stop` can end it."""
        process = subprocess.Popen(command, **kwargs)
        self._browser_process = process
        try:
            process.wait()
        finally:
            self._browser_process = None

    def stop(self) -> None:
        self._stop.set()
        # A browse still on screen is the session's window, so it goes with the
        # session — otherwise quitting Fun Time leaves the grid up over an empty
        # desktop, owned by nothing that is still running.
        browsing = self._browser_process
        if browsing is not None:
            browsing.terminate()


# --- the window-op interpreter ----------------------------------------------
# One handler per Op member, checked complete at import: a new op without a
# handler fails startup, not the first press.  Only the two hotkey-suspension
# verbs may pass through to ahk_cmd.txt — AHK ignores every other string.

_AHK_PASSTHROUGH_OPS = {Op.SUSPEND_HOTKEYS, Op.UNSUSPEND_HOTKEYS}


def _run_show_role(runner: "DispatchLoopRunner", op: WindowOp) -> None:
    runner.windows.show(op.key)


def _run_hide_role(runner: "DispatchLoopRunner", op: WindowOp) -> None:
    runner.windows.hide_after_settle(op.key)


def _run_minimize_role(runner: "DispatchLoopRunner", op: WindowOp) -> None:
    # A player's own HUD minimize button, parking that one window — straight
    # away, unlike the settled main-slot hide: the window goes down still
    # showing its video, which is what was pressed for, and its frozen
    # thumbnail is then the clip it holds.
    runner.windows.park(op.key)


def _run_restore_parked(runner: "DispatchLoopRunner", _op: WindowOp) -> None:
    runner.windows.restore_parked()


def _run_activate_role(runner: "DispatchLoopRunner", op: WindowOp) -> None:
    if os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1":
        runner.windows.activate(op.key)


def _run_restack_main(runner: "DispatchLoopRunner", _op: WindowOp) -> None:
    # Re-stack the overlapping Nau/Genau pair for the current mode.  Not
    # integration-guarded: SetWindowPos(HWND_TOPMOST) uses SWP_NOACTIVATE, so
    # it changes only the z-band, never focus.
    runner.windows.restack_main_slot(runner.state.main_mode)


def _run_restack_satellites(runner: "DispatchLoopRunner", _op: WindowOp) -> None:
    runner.windows.restack_satellites(
        runner.state.main_mode, runner.state.satellites_mode)


def _run_disable_all_topmost(runner: "DispatchLoopRunner", _op: WindowOp) -> None:
    runner.windows.remove_all_topmost()


def _run_restore_all_topmost(runner: "DispatchLoopRunner", _op: WindowOp) -> None:
    runner.windows.restore_all_topmost(
        runner.state.main_mode, runner.state.satellites_mode)


def _run_open_rfb_tab(runner: "DispatchLoopRunner", op: WindowOp) -> None:
    runner._pending_rfb_urls.append(op.key)


def _run_save_clip(runner: "DispatchLoopRunner", _op: WindowOp) -> None:
    # Slow work runs beside the loop, like the browse and the broker toggles;
    # the thread flashes the result when the save lands.
    threading.Thread(
        target=runner._handle_clipper_save,
        daemon=True,
        name="clipper-save",
    ).start()


def _run_notice(_runner: "DispatchLoopRunner", op: WindowOp) -> None:
    notice(logger, op.key, source=op.source, level=op.level)


def _run_ahk_passthrough(runner: "DispatchLoopRunner", op: WindowOp) -> None:
    if (op.op == Op.UNSUSPEND_HOTKEYS
            and os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1"):
        return
    runner.ahk_cmd_file.write_text(op.op, encoding="utf-8")


_OP_HANDLERS = {
    Op.NOTICE: _run_notice,
    Op.SHOW_ROLE: _run_show_role,
    Op.HIDE_ROLE: _run_hide_role,
    Op.ACTIVATE_ROLE: _run_activate_role,
    Op.MINIMIZE_ROLE: _run_minimize_role,
    Op.RESTORE_PARKED: _run_restore_parked,
    Op.RESTACK_MAIN: _run_restack_main,
    Op.RESTACK_SATELLITES: _run_restack_satellites,
    Op.DISABLE_ALL_TOPMOST: _run_disable_all_topmost,
    Op.RESTORE_ALL_TOPMOST: _run_restore_all_topmost,
    Op.SUSPEND_HOTKEYS: _run_ahk_passthrough,
    Op.UNSUSPEND_HOTKEYS: _run_ahk_passthrough,
    Op.OPEN_RFB_TAB: _run_open_rfb_tab,
    Op.SAVE_CLIP: _run_save_clip,
}
assert set(_OP_HANDLERS) == set(Op), "every window op needs a handler"


def build_bridge_config_from_manifest(
    manifest: LaunchManifest,
    *,
    vr_main_player: bool = False,
) -> BridgeConfig:
    """Build a BridgeConfig from the session's launch manifest.

    *vr_main_player* says the session hosts its main player inside the VR
    scene rather than launching Nau.  The manifest cannot answer it — both
    sessions build from the same one — so the orchestrator that knows says so.
    """
    commands = manifest.commands
    return BridgeConfig(
        vr_main_player=vr_main_player,
        portrait_cmd_file=Path(commands.portrait_cmd_file),
        portrait_paused_file=Path(commands.portrait_paused_file),
        portrait_status_file=Path(commands.portrait_status_file),
        portrait_playlist_file=Path(commands.portrait_playlist_file),
        landscape_cmd_file=Path(commands.landscape_cmd_file),
        landscape_paused_file=Path(commands.landscape_paused_file),
        landscape_status_file=Path(commands.landscape_status_file),
        landscape_playlist_file=Path(commands.landscape_playlist_file),
        favs_file=Path(manifest.media.favs_file),
        weird_dir=Path(manifest.media.weird_dir),
        state_dir=Path(commands.dashboard_state_file).parent,
        main_sources=manifest.media.nau_library_sources,
        python_exe=manifest.executables.python_exe,
        portrait_sources=manifest.media.portrait_dirs,
        landscape_sources=manifest.media.landscape_dirs,
        genau_mode_file=Path(commands.genau_mode_file),
        genau_cmd_file=Path(commands.genau_cmd_file),
        genau_paused_file=Path(commands.genau_paused_file),
        audio_paused_file=Path(commands.audio_paused_file),
        audio_volume_file=Path(commands.audio_volume_file),
        nau_cmd_file=Path(commands.nau_cmd_file),
        nau_paused_file=Path(commands.nau_paused_file),
        nau_status_file=Path(commands.nau_status_file),
        nau_notice_file=Path(commands.nau_status_file).with_name("nau_notice.txt"),
        dashboard_state_file=Path(commands.dashboard_state_file),
        broker_cmd_file=Path(commands.broker_cmd_file),
        broker_heartbeat_file=Path(commands.broker_heartbeat_file),
        broker_state_dir=Path(v) if (v := commands.broker_state_dir.strip()) else None,
        broker_tray_launcher=Path(v) if (v := commands.broker_tray_launcher.strip()) else None,
        regen_media_root=Path(v) if (v := manifest.regen.media_root.strip()) else None,
        regen_metadata_root=Path(v) if (v := manifest.regen.metadata_root.strip()) else None,
        regen_generate_video_url=manifest.regen.generate_video_url,
        regen_generate_image_url=manifest.regen.generate_image_url,
        origenerator_enabled=bool(manifest.runtime.origenerator_dir.strip()),
        origenerator_cmd_file=Path(v) if (v := commands.origenerator_cmd_file.strip()) else None,
        origenerator_paused_file=Path(v) if (v := commands.origenerator_paused_file.strip()) else None,
    )
