"""Background dispatch loop for dashboard commands and genau sync.

Runs in a thread alongside the AHK hotkey script, handling periodic
dispatch directly in Python instead of spawning subprocesses.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from app_support import osr2_device
from app_support.file_channel import consume_command_file, read_flag
from app_support.threading_utils import start_daemon_thread
from app_support.win32 import force_foreground_window, window_exists
from funestra import broker
from funestra.console import BROKER_PRESS
from funestra.file_channel import append_command
from funestra.funestra_verbs import play_file
from funestra.playlist import PlaylistItem

from .aiming import notice_source, resolve_active_funestra_command
from .arrivals import keep_looking
from .bridge_records import BridgeConfig, Op, WindowOp
from .child_launch import no_child_log
from .command_dispatch import dispatch_command, room_at_defaults, routes_to_origenerator
from .crown import Seating
from .dashboard_actions import (
    BROWSE_LIBRARY_CLOSE,
    ENTER_OMNIPAUSE,
    HEADSET_OFF,
    HEADSET_SIGNALS,
    HELP_REFERENCE,
    HELP_REFERENCE_COMMANDS,
    LIBRARY_OPEN_FILENAME,
    REFERENCE_OPEN_FILENAME,
)
from .dashboard_bridge import DashboardSnapshot, write_dashboard_snapshot
from .device_arbiter import DeviceArbiter, SatelliteLine, who_drives_the_osr2
from .event_log import NOTICE, SOURCE_SYSTEM, notice
from .f_mode import every_funestra_in_f_mode
from .funestra_handover import Homecoming
from .funestra_notices import FunestraNotices
from .funestra_settings import FunestraReporting
from .funestra_status import read_genau_status, read_main_funestra_status
from .genaumacher_save import save_and_say
from .headset_power import the_headset_is_ready
from .hosted_contract import said_at_suffix_of
from .hud_feed import HudFeed
from .hud_transport import HudPublisher
from .latest_arrivals import LatestArrivals
from .library_browser import browse_library
from .lock import a_lock_holds
from .lock_follower import LockFollower, what_the_main_slot_holds
from .loopback_inbox import PRESS_PORT_FILENAME, post_to_inbox
from .main_list_builds import BuildsOffTheLoop
from .manifest import WINDOWS_BRIDGE_MANIFEST_FILENAME, LaunchManifest
from .mode_plan import kino_shows
from .modes import scripted_item
from .origenerator_opening import OrigeneratorOpening, holding_nothing
from .origenerator_switches import RfbSlot, hosts
from .rfb_slideshow import RfbSlideshow
from .role_windows import WindowRoles
from .room_funestras import RoomFunestra
from .runtime_flow import write_flag_file
from .satellite_speeds import SatelliteSpeeds
from .session_end import mark_session_end
from .session_environment import ORDINARY_SESSION, SessionEnvironment
from .session_handoff import DESKTOP, VR, HandoffTarget, request_a_crossing
from .shared_state import BridgeState, read_shared_state, write_shared_state
from .shortcuts import Shortcut
from .text_field_clicks import Sightings, TypingWatch
from .voice_commands import CommandLine, parse_command_line
from .voice_control import NOTHING_TO_ACCEPT, RoomVoice
from .watch_sampling import WatchSampler
from .watch_stats import watch_stats_path
from .win32 import find_window_for_process, set_always_on_top, window_rect
from .window_layout import SecondaryMonitorRects
from .window_roles import visible_roles
from .windows_bridge_random_favs_browser import open_rfb_tab

logger = logging.getLogger(__name__)


def keep_a_browse_on_top(browsing: subprocess.Popen | None) -> int:
    # HWND_TOPMOST inserts at the band's TOP, so run this after the ops it outranks.
    if browsing is None or browsing.poll() is not None:
        return 0
    hwnd = find_window_for_process(browsing.pid)
    if hwnd:
        set_always_on_top(hwnd, True)
    return hwnd


def poll_dashboard_commands(cmd_file: Path) -> list[str]:
    """Claim the dashboard command file and return every queued command, as spelled.

    The family's consumer, with case kept: a dashboard verb can carry a path.
    """
    return consume_command_file(cmd_file, uppercase=False)


HANDOFF_COMMANDS: dict[str, HandoffTarget] = {"enter_vr": VR, "exit_vr": DESKTOP}
REQUEST_NEWS: dict[str, tuple[str, bool]] = {
    f"{side}_request_{news}": (side, opened)
    for side in ("portrait", "landscape")
    for news, opened in (("open", True), ("closed", False))
}


_FUNESTRA_GROUPS = {
    "both_": ("portrait_", "landscape_"),
    "all_": ("main_", "portrait_", "landscape_"),
}


def expand_group_command(command: str) -> list[str]:
    """A ``both_``/``all_`` command as the per-Funestra commands it stands for,
    each then handled exactly as if said alone; anything else as itself."""
    for group, funestras in _FUNESTRA_GROUPS.items():
        if command.startswith(group):
            return [funestra + command[len(group):] for funestra in funestras]
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
        env: SessionEnvironment = ORDINARY_SESSION,
        manifest_path: Path | None = None,
        hud_publisher: HudPublisher | None = None,
        rfb_shortcut: Shortcut | None = None,
        rfb_slideshow: RfbSlideshow | None = None,
        press_and_caret_watch: Sightings | None = None,
        sync_interval_ms: int = 200,
        origenerator_already_open: bool = False,
        origenerator_slideshow_on: tuple[RoomFunestra, ...] = (),
        secondary_rects: Callable[..., SecondaryMonitorRects] | None = None,
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
        self.seating = Seating(secondary_rects,
                               main_funestra_status_file=config.main_funestra_status_file,
                               genau_status_file=config.genau_status_file)
        self.dashboard_enabled = dashboard_enabled
        self.env = env
        # This loop holds the state each Funestra's own HUD is drawn from (locks,
        # filters, loops) and already ticks, so it is what feeds them.
        self.hud = HudFeed(config=config, publisher=hud_publisher)
        self.rfb_shortcut = rfb_shortcut or Shortcut()
        self.rfb_slideshow = rfb_slideshow
        self.typing = TypingWatch(press_and_caret_watch, typed_into=lambda seen: self.windows.takes_typing(
            seen.window, seen.process, hosted_app=self.config.origenerator_enabled))
        self.sync_interval_s = sync_interval_ms / 1000
        left_in = read_shared_state(shared_state_file) or BridgeState()
        self.state = holding_nothing(left_in)
        self.opening = OrigeneratorOpening(config, left_in, already_open=origenerator_already_open,
                                           slideshow_on=origenerator_slideshow_on)
        self._last_sync = 0.0
        self._last_dashboard_warning = float("-inf")
        self._stop = threading.Event()
        self._browse_lock = threading.Lock()
        # The browse now on screen, if any.  It is launched mid-session, so it
        # cannot join the startup children the teardown list kills; this holds it
        # instead, and stop() is where quitting takes it with the session.
        self._browser_process: subprocess.Popen | None = None
        self._pending_rfb_urls: list[str] = []
        self._batching_rfb = False
        self.notices = FunestraNotices(config)
        self.reporting = FunestraReporting()
        self.voice = RoomVoice()
        self._answers = 0
        self._ignored_because = ""
        self.homecoming = Homecoming()
        self.watch = WatchSampler(
            main_funestra_status_file=config.main_funestra_status_file,
            satellite_status_files={2: config.portrait_status_file,
                                    3: config.landscape_status_file},
            stats_file=watch_stats_path(config.state_dir),
        )
        self.arbiter = DeviceArbiter(
            main_funestra_status_file=config.main_funestra_status_file,
            main_funestra_cmd_file=config.main_funestra_cmd_file,
            genau_cmd_file=config.genau_cmd_file,
            device_on=lambda: osr2_device.switched_on(config.broker_state),
            satellites={funestra: SatelliteLine(status_file=config.satellite(funestra).status_file,
                                              cmd_file=config.satellite(funestra).cmd_file)
                        for funestra in RoomFunestra.SATELLITES},
        )
        self.satellite_speeds = SatelliteSpeeds(
            main_funestra_status_file=config.main_funestra_status_file,
            satellite_cmd_files=tuple(
                config.satellite(funestra).cmd_file for funestra in RoomFunestra.SATELLITES),
        )
        self.lock_follower = LockFollower(what_the_main_slot_holds(config))
        self.latest_arrivals = LatestArrivals(config)

    def activate_origenerator(self, *, wait_for_the_app) -> None:
        owed = self.opening.take_what_is_owed(wait_for_the_app, self.shared_state_file)
        if owed:
            self.state = self.opening.room_as_it_is(self.state)
        for command in owed:
            self._dispatch(command)

    def tick(self) -> None:
        """Run one iteration: poll dashboard, maybe sync genau."""
        self.notices.flash_new()

        shared = read_shared_state(self.shared_state_file)
        if shared is not None:
            self.state = shared
        # File or no file: this corrects a reading of the room, not the file.
        self.state = self.opening.room_as_it_is(self.state)
        self.state = self.reporting.recorded(self.state, self.config, now=time.monotonic())
        if shared is not None and shared != self.state:
            # Nothing else writes the file until a command is dispatched, which on
            # a session nobody is touching may be never.
            write_shared_state(self.shared_state_file, self.state)

        self.arbiter.follow(self.state)

        # Dashboard commands, several at once under rapid hotkey presses.  Each
        # raw line yields a command plus, for a spoken one, when the utterance
        # began; an "active_*" command then resolves onto the Funestra most
        # recently addressed and a "both_*" one expands into its pair.
        self._batching_rfb = True
        try:
            for line in poll_dashboard_commands(self.dashboard_cmd_file):
                self._handle_line(parse_command_line(line))
        finally:
            self._batching_rfb = False
        self._pause_for_a_click_into_a_text_field()
        self._flush_rfb_tabs()
        # After the batch, so a switch and a switch straight back inside one
        # batch cancel rather than minimize the Funestra they just brought back.
        self.windows.flush_pending_hides()
        seated = self.seating.seat(self.state, self.windows)
        if seated is not self.state:
            self.state = seated
            write_shared_state(self.shared_state_file, self.state)

        self.voice.suspend_as_the_room_is(paused=self.state.omni_paused)

        # Periodic sync: z-order enforcement, the satellites' rate, the dashboard
        now = time.monotonic()
        if now - self._last_sync >= self.sync_interval_s:
            self._last_sync = now
            self._converge_origenerator_window()
            self.satellite_speeds.take_the_main_funestras_rate()
            if self.dashboard_enabled:
                self._update_dashboard()
        self.homecoming.bring(self.state, self.config, now=now)
        self.latest_arrivals.deliver(self.state, coming_home=self.homecoming.funestras)
        held = self.lock_follower.held()
        if held:
            self._take(*a_lock_holds(held, self.state, self.config))
        self.watch.sample_due(now=now, paused=self.state.omni_paused,
                              hosted=tuple(funestra for funestra in RoomFunestra.SATELLITES
                                           if hosts(funestra, self.state, self.config)))
        self.hud.publish_due(self.state, now=now)
        if self.rfb_slideshow is not None:
            self.rfb_slideshow.tick(now=now, held=self.state.omni_paused
                                    or self.state.rfb_slot is RfbSlot.ORIGENERATOR)

    def _pause_for_a_click_into_a_text_field(self) -> None:
        if not self.typing.clicked_into_a_text_field(now=time.monotonic()):
            return
        if not self.state.omni_paused:
            logger.info("A click into a text field enters OmniPause")
            self._handle_command(ENTER_OMNIPAUSE)

    def hear_as_a_lock(self, phrases: frozenset[str]) -> None:
        self.config = replace(self.config, origenerator_lock_phrases=phrases)

    def _handle_line(self, line: CommandLine) -> None:
        source = notice_source(line.command, self.state.active_funestra)
        answers_before = self._answers
        self._ignored_because = ""
        resolved = resolve_active_funestra_command(line.command, self.state.active_funestra)
        for command in expand_group_command(resolved):
            self._handle_command(command, line.spoken_at)
        if not line.said:
            return
        if self._ignored_because:
            self._flash(f"ignored {self._ignored_because}: {line.said}", source=source,
                        level=logging.WARNING)
        elif self._answers == answers_before:
            self._flash(line.said, source=source)

    def _flash(self, message: str, *, source: str, level: int = NOTICE) -> None:
        self._answers += 1
        notice(logger, message, source=source, level=level)

    def _handle_command(self, cmd: str, spoken_at: float | None = None) -> None:
        """Route one polled command (already expanded from any ``both_*``).

        ``spoken_at`` is when a voice command's utterance began, and None for
        the instantaneous hotkey and dashboard presses.
        """
        if cmd in HEADSET_SIGNALS:
            _headset(self, cmd)
            return
        self._send_press(cmd)
        answer = _LOOP_COMMANDS.get(cmd)
        if answer is None:
            self._dispatch(cmd, spoken_at)
        else:
            answer(self, spoken_at)

    def _dispatch(self, command: str, spoken_at: float | None = None) -> None:
        logger.info("Dispatching command: %s", command)
        # A press bound for an Origenerator show steps that show, and booking
        # it here would count a library clip as skipped or discarded.
        if not routes_to_origenerator(command, self.state, self.config):
            self.watch.note_command(command)
        self._take(*dispatch_command(
            command, self.state, self.config,
            target_path=self.watch.video_at(command, spoken_at),
            spoken_at=spoken_at,
        ))

    def _take(self, new_state: BridgeState, ops: list[WindowOp]) -> None:
        before, self.state = self.state, new_state
        if who_drives_the_osr2(before) != who_drives_the_osr2(new_state):
            self.arbiter.follow(new_state)
        for op in ops:
            handler = _OP_HANDLERS.get(op.op)
            if handler is None:
                # Never the AHK fall-through: an op the interpreter does not
                # know is a bug here, not a verb for the hotkey script.
                logger.error("unhandled window op %r", op.op)
                continue
            handler(self, op)
        keep_a_browse_on_top(self._browser_process)
        write_shared_state(self.shared_state_file, self.state)
        if not self._batching_rfb:
            self._flush_rfb_tabs()
        if self.dashboard_enabled:
            self._update_dashboard()

    def _flush_rfb_tabs(self) -> None:
        """Open every buffered RFB URL as tabs in the session's own Chrome window.

        The RFB runs in the user's own Chrome profile, so his personal windows
        are candidates for the tab too: Chrome's ``FindTabbedBrowser`` walks
        that profile's windows most-recently-active first, so the window he
        touched last would win and the lock's tab would land under the
        Funestras.  Activating the RFB window first is what settles it — it goes
        to the head of Chrome's own activation order, and Chrome shows the
        window it opens into either way, so this only decides which one rises.

        A dead handle means Fun Time has no window of its own left to open
        into, and every URL handed over then lands in one of his: worth losing
        the tab over, so the launch is skipped entirely.

        While Origenerator holds the browser's place the buffer holds instead
        of flushing: opening a tab would force Chrome over it.  The locks queue,
        and putting the browser back in its place flushes them.
        """
        if self.state.rfb_slot is RfbSlot.ORIGENERATOR:
            return
        urls = self._pending_rfb_urls
        self._pending_rfb_urls = []
        if not urls or not self.rfb_shortcut.target:
            return
        if not window_exists(self.windows.rfb_hwnd):
            logger.warning(
                "%d RFB tab(s) skipped: no Random Favs Browser window to open into", len(urls))
            return
        if not force_foreground_window(self.windows.rfb_hwnd):
            # Not fatal, and expected on the integration suite's hidden desktop,
            # which has no foreground window to become.
            logger.info("RFB window did not take the foreground before the tab handoff")
        open_rfb_tab(urls=urls, shortcut=self.rfb_shortcut)
        logger.info("Opened %d RFB tab(s)", len(urls))
        if self.rfb_slideshow is not None:
            self.rfb_slideshow.restart(now=time.monotonic())

    def _send_press(self, action: str) -> None:
        if self.dashboard_enabled:
            post_to_inbox(self.config.state_dir / PRESS_PORT_FILENAME, action)

    def _update_dashboard(self) -> None:
        """Write the dashboard's snapshot; only the disk may fail quietly.

        OSError (the state file held open by a reader) warns, throttled to once
        per failing minute since this runs twice a second; anything else is a
        bug of ours that the loop's per-tick exception log must show.
        """
        try:
            write_dashboard_snapshot(
                str(self.config.dashboard_state_file),
                DashboardSnapshot(
                    omni_paused=self.state.omni_paused,
                    voice_active=self.voice.active,
                    f_mode=every_funestra_in_f_mode(self.state, self.config),
                    in_vr=self.config.vr_main_funestra,
                    nothing_to_reset=room_at_defaults(
                        self.state, self.config,
                        read_main_funestra_status(self.config.main_funestra_status_file)),
                    headset_ready=the_headset_is_ready(self.config.state_dir),
                    origenerator_ready=(self.config.origenerator_enabled
                                        and self.state.origenerator_ready),
                    rfb_slot=self.state.rfb_slot,
                ),
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
        self.windows.converge_origenerator_window(self.state.rfb_slot)

    def _publish_panel(self, filename: str, *, toggling: bool) -> None:
        path = Path(self.config.state_dir) / filename
        write_flag_file(path, toggling and not read_flag(path, default=False))

    def _log_topmost_state(self, label: str) -> None:
        logger.info("Topmost [%s] mode=%s: %s", label, self.state.main_mode,
                    self.windows.topmost_report())

    def _handle_browse_library(self) -> None:
        """Browse the library and play the pick in the Main Funestra, one browse at a time.

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
        # Browsing keeps everything playing -- it must NOT enter OmniPause (a
        # pause here once stranded the satellites and voice frozen).  The browser
        # needs only not to be buried under the always-on-top windows and not to
        # lose its arrow keys to the global hotkeys, so the bands drop and the
        # keys suspend for its duration.  Under OmniPause both are already down
        # and the pause owns that hold, so it is left to release it.
        manage_session = not self.state.omni_paused

        if manage_session:
            self.windows.remove_all_topmost()
            append_command(self.ahk_cmd_file, Op.SUSPEND_HOTKEYS)

        try:
            # Over the main slot's window: the pick plays there, so the browse
            # stands where it will, and covers nothing else on either monitor.
            genau = not kino_shows(self.state.main_mode)
            funestra_hwnd = self.windows.hwnd("main_player")
            selected = browse_library(
                self.manifest_path,
                self.config.python_exe,
                over=window_rect(funestra_hwnd) if funestra_hwnd else None,
                playing=(read_genau_status(self.config.genau_status_file).flick if genau
                         else read_main_funestra_status(self.config.main_funestra_status_file).video),
                flicks=genau,
                runner=self._run_browser,
            )
            if selected and genau:
                append_command(self.config.genau_cmd_file, play_file(PlaylistItem(Path(selected))))
            elif selected:
                append_command(self.config.main_funestra_cmd_file, play_file(scripted_item(selected, self.config.regen_metadata_root)))
        finally:
            if manage_session:
                self.windows.restore_all_topmost(self.state.rfb_slot)
                append_command(self.ahk_cmd_file, Op.UNSUSPEND_HOTKEYS)

    def run(self) -> None:
        """Main loop — call from a background thread."""
        # Log the topmost state once the loop starts so a "windows not all on top
        # after launch" report can be pinned to the exact window that missed its
        # startup promotion.
        self._log_topmost_state("startup")
        self.typing.start()
        start_daemon_thread(target=keep_looking, args=(self.latest_arrivals.look, self._stop),
                            name="latest-arrivals")
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
        process = subprocess.Popen(command, **no_child_log(), **kwargs)
        self._browser_process = process
        try:
            process.wait()
        finally:
            self._browser_process = None

    def stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        if self.rfb_slideshow is not None:
            self.rfb_slideshow.stop()
        self.typing.stop()
        browsing = self._browser_process
        if browsing is not None:
            browsing.terminate()


# --- the loop's own commands --------------------------------------------------
# What needs the loop's live objects -- its windows, its voice, the browse, the
# broker, the hotkey script -- is answered here by name; everything else is
# command_dispatch's, a state in and window ops out.

def _quit(runner: DispatchLoopRunner, spoken_at: float | None) -> None:
    # Said, because a mis-heard word otherwise reads exactly like a
    # deliberate click.
    mark_session_end(
        runner.config.state_dir,
        "the quit command, spoken" if spoken_at is not None else "the quit command, pressed",
    )
    runner.ahk_cmd_file.write_text("end_session", encoding="utf-8")


def _cross(target: HandoffTarget, runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    """Cross to the other session, ending this one as "quit" does."""
    refused = request_a_crossing(target, runner.config.state_dir,
                                 vr_main_funestra=runner.config.vr_main_funestra)
    if refused:
        runner._flash(refused, source=SOURCE_SYSTEM, level=logging.WARNING)
        return
    mark_session_end(runner.config.state_dir, f"a crossing to {target.app_name}")
    runner.ahk_cmd_file.write_text("end_session", encoding="utf-8")


def _show_the_reference(toggling: bool, runner: DispatchLoopRunner,
                        _spoken_at: float | None) -> None:
    # A dashboard-UI action: the press tells a desktop dashboard, the flag tells
    # a headset, and there is nothing to dispatch.
    runner._publish_panel(REFERENCE_OPEN_FILENAME, toggling=toggling)


def _omniminimize(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    """Minimize the windows the modes show, and only those (SW_MINIMIZE would
    drag a parked window back into view); the set is remembered so omnirestore
    brings back exactly these windows."""
    runner.windows.minimize_all(visible_roles(runner.state.rfb_slot))


def _omnirestore(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    runner.windows.restore_minimized()


def _toggle_omnipause(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    was_paused = runner.state.omni_paused
    runner._dispatch("omnipause_toggle")
    runner._log_topmost_state("post-leave" if was_paused else "post-enter")


def _enter_omnipause(command: str, runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    """*command* is the way in: ``enter_omnipause`` (Space) parks the OSR2,
    ``relief_omnipause`` (Shift+Esc) retracts it instead."""
    runner._dispatch(command)
    runner._log_topmost_state("post-enter")


def _enter_omnipause_unless_paused(runner: DispatchLoopRunner, spoken_at: float | None) -> None:
    if not runner.state.omni_paused:
        _enter_omnipause(ENTER_OMNIPAUSE, runner, spoken_at)


def _pause(runner: DispatchLoopRunner, spoken_at: float | None) -> None:
    if not runner.state.omni_paused:
        _toggle_omnipause(runner, spoken_at)


def _play(runner: DispatchLoopRunner, spoken_at: float | None) -> None:
    if runner.state.omni_paused:
        _toggle_omnipause(runner, spoken_at)


def _browse_the_library(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    if runner.config.vr_main_funestra:
        runner._publish_panel(LIBRARY_OPEN_FILENAME, toggling=True)
    else:
        threading.Thread(
            target=runner._handle_browse_library,
            daemon=True,
            name="library-browser",
        ).start()


def _close_the_browse(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    runner._publish_panel(LIBRARY_OPEN_FILENAME, toggling=False)


def _press_the_broker(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    broker.press(runner.config.broker_state, runner.config.broker_tray_launcher)


def _start_the_broker(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    """Start the broker if none is running: a start, never a restart."""
    if not broker.is_running(runner.config.broker_state):
        broker.start(runner.config.broker_tray_launcher)


def _stop_the_broker(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    if broker.is_running(runner.config.broker_state):
        broker.stop(runner.config.broker_state, runner.config.broker_tray_launcher)


def _toggle_voice(command: str, runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    """Mute or toggle voice control, then refresh the dashboard."""
    muted = runner.voice.toggle(command)
    if muted is None:
        return
    runner.state = replace(runner.state, mic_muted=muted)
    write_shared_state(runner.shared_state_file, runner.state)
    if runner.dashboard_enabled:
        runner._update_dashboard()


def _accept_what_voice_was_not_sure_of(runner: DispatchLoopRunner,
                                       _spoken_at: float | None) -> None:
    if not runner.voice.accept():
        runner._flash(NOTHING_TO_ACCEPT, source=SOURCE_SYSTEM, level=logging.WARNING)


def _pass_on_request_news(side: str, opened: bool, runner: DispatchLoopRunner,
                          _spoken_at: float | None) -> None:
    runner.voice.request_news(side, opened)


def _headset(runner: DispatchLoopRunner, signal: str) -> None:
    off = signal == HEADSET_OFF
    if not runner.voice.headset_is(off=off):
        return
    logger.info("The headset is %s", "off: only quit is heard" if off else "back on")
    append_command(runner.ahk_cmd_file, signal)
    runner.voice.suspend_as_the_room_is(paused=runner.state.omni_paused)


_LOOP_COMMANDS: dict[str, Callable[[DispatchLoopRunner, float | None], None]] = {
    "quit": _quit,
    **{command: partial(_cross, target) for command, target in HANDOFF_COMMANDS.items()},
    **{command: partial(_show_the_reference, command == HELP_REFERENCE)
       for command in HELP_REFERENCE_COMMANDS},
    "omniminimize": _omniminimize,
    "omnirestore": _omnirestore,
    "omnipause_toggle": _toggle_omnipause,
    ENTER_OMNIPAUSE: _enter_omnipause_unless_paused,
    "relief_omnipause": partial(_enter_omnipause, "relief_omnipause"),
    "browse_library": _browse_the_library,
    BROWSE_LIBRARY_CLOSE: _close_the_browse,
    BROKER_PRESS: _press_the_broker,
    "pause": _pause,
    "play": _play,
    "broker_start": _start_the_broker,
    "broker_stop": _stop_the_broker,
    "voice_off": partial(_toggle_voice, "voice_off"),
    "voice_toggle": partial(_toggle_voice, "voice_toggle"),
    "voice_accept": _accept_what_voice_was_not_sure_of,
    **{command: partial(_pass_on_request_news, side, opened)
       for command, (side, opened) in REQUEST_NEWS.items()},
}


# --- the window-op interpreter ----------------------------------------------
# One handler per Op item, checked complete at import: a new op without a
# handler fails startup, not the first press.  Only the two hotkey-suspension
# verbs may pass through to ahk_cmd.txt — AHK ignores every other string.

_AHK_PASSTHROUGH_OPS = {Op.SUSPEND_HOTKEYS, Op.UNSUSPEND_HOTKEYS}


def _run_show_role(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner.windows.show(op.key)


def _run_hide_role(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner.windows.hide_after_settle(op.key)


def _run_minimize_role(runner: DispatchLoopRunner, op: WindowOp) -> None:
    # A Funestra's own HUD minimize button, parking that one window — straight
    # away, unlike the settled main-slot hide: the window goes down still
    # showing its video, which is what was pressed for, and its frozen
    # thumbnail is then the clip it holds.
    runner.windows.park(op.key)


def _run_restore_parked(runner: DispatchLoopRunner, _op: WindowOp) -> None:
    runner.windows.restore_parked()


def _run_activate_role(runner: DispatchLoopRunner, op: WindowOp) -> None:
    if not runner.env.integration:
        runner.windows.activate(op.key)


def _run_restack_rfb(runner: DispatchLoopRunner, _op: WindowOp) -> None:
    runner.windows.restack_rfb_slot(runner.state.rfb_slot, paused=runner.state.omni_paused)


def _run_disable_all_topmost(runner: DispatchLoopRunner, _op: WindowOp) -> None:
    runner.windows.remove_all_topmost()


def _run_restore_all_topmost(runner: DispatchLoopRunner, _op: WindowOp) -> None:
    runner.windows.restore_all_topmost(runner.state.rfb_slot)


def _run_open_rfb_tab(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner._pending_rfb_urls.append(op.key)


def _run_save_clip(runner: DispatchLoopRunner, _op: WindowOp) -> None:
    # Slow work runs beside the loop, like the browse; the thread flashes the
    # result when the save lands.
    runner._answers += 1
    threading.Thread(
        target=save_and_say,
        args=(runner.config,),
        daemon=True,
        name="genaumacher-save",
    ).start()


def _run_an_answer_given_elsewhere(runner: DispatchLoopRunner, _op: WindowOp) -> None:
    runner._answers += 1


def _run_ignored(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner._ignored_because = op.key


def _run_notice(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner._flash(op.key, source=op.source, level=op.level)


def _run_take_back_funestras(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner.homecoming.expect((RoomFunestra[op.key.upper()],), runner.config,
                             now=time.monotonic())


def _run_follow_the_lock(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner.lock_follower.follow(op.key)


def _run_ahk_passthrough(runner: DispatchLoopRunner, op: WindowOp) -> None:
    if op.op == Op.UNSUSPEND_HOTKEYS and runner.env.integration:
        return
    append_command(runner.ahk_cmd_file, op.op)


_OP_HANDLERS = {
    Op.NOTICE: _run_notice,
    Op.SHOW_ROLE: _run_show_role,
    Op.HIDE_ROLE: _run_hide_role,
    Op.ACTIVATE_ROLE: _run_activate_role,
    Op.MINIMIZE_ROLE: _run_minimize_role,
    Op.RESTORE_PARKED: _run_restore_parked,
    Op.RESTACK_RFB: _run_restack_rfb,
    Op.DISABLE_ALL_TOPMOST: _run_disable_all_topmost,
    Op.RESTORE_ALL_TOPMOST: _run_restore_all_topmost,
    Op.SUSPEND_HOTKEYS: _run_ahk_passthrough,
    Op.UNSUSPEND_HOTKEYS: _run_ahk_passthrough,
    Op.OPEN_RFB_TAB: _run_open_rfb_tab,
    Op.SAVE_CLIP: _run_save_clip,
    Op.MAIN_FUNESTRA_ANSWERS: _run_an_answer_given_elsewhere,
    Op.NARROWING_ANSWERS: _run_an_answer_given_elsewhere,
    Op.IGNORED: _run_ignored,
    Op.TAKE_BACK_FUNESTRAS: _run_take_back_funestras,
    Op.FOLLOW_THE_LOCK: _run_follow_the_lock,
}
assert set(_OP_HANDLERS) == set(Op), "every window op needs a handler"


def build_bridge_config_from_manifest(
    manifest: LaunchManifest,
    *,
    vr_main_funestra: bool = False,
) -> BridgeConfig:
    """Build a BridgeConfig from the session's launch manifest.

    *vr_main_funestra* says the session hosts its Main Funestra inside the VR
    scene rather than launching the Main Funestra.  The manifest cannot answer it — both
    sessions build from the same one — so the orchestrator that knows says so.
    """
    commands = manifest.commands
    return BridgeConfig(
        vr_main_funestra=vr_main_funestra,
        main_list_builds=BuildsOffTheLoop(),
        portrait_cmd_file=Path(commands.portrait_cmd_file),
        portrait_paused_file=Path(commands.portrait_paused_file),
        portrait_status_file=Path(commands.portrait_status_file),
        portrait_playlist_file=Path(commands.portrait_playlist_file),
        landscape_cmd_file=Path(commands.landscape_cmd_file),
        landscape_paused_file=Path(commands.landscape_paused_file),
        landscape_status_file=Path(commands.landscape_status_file),
        landscape_playlist_file=Path(commands.landscape_playlist_file),
        weird_dir=Path(manifest.media.weird_dir),
        state_dir=Path(commands.state_dir),
        loopback_port=manifest.loopback_port,
        main_sources=manifest.media.main_funestra_library_sources,
        vr_library_dirs=manifest.media.vr_library_dirs,
        genau_vr_flicks=manifest.media.genau_vr_flicks,
        python_exe=manifest.executables.python_exe,
        portrait_sources=manifest.media.portrait_dirs,
        landscape_sources=manifest.media.landscape_dirs,
        broker_mode_file=Path(commands.broker_mode_file),
        genau_cmd_file=Path(commands.genau_cmd_file),
        genau_paused_file=Path(commands.genau_paused_file),
        genau_status_file=Path(commands.genau_status_file),
        audio_paused_file=Path(commands.audio_paused_file),
        audio_volume_file=Path(commands.audio_volume_file),
        main_funestra_cmd_file=Path(commands.main_funestra_cmd_file),
        main_funestra_paused_file=Path(commands.main_funestra_paused_file),
        main_funestra_status_file=Path(commands.main_funestra_status_file),
        main_funestra_notice_file=Path(commands.main_funestra_notice_file),
        portrait_notice_file=Path(commands.portrait_notice_file),
        landscape_notice_file=Path(commands.landscape_notice_file),
        dashboard_state_file=Path(commands.dashboard_state_file),
        broker_cmd_file=Path(commands.broker_cmd_file),
        broker_state_dir=Path(v) if (v := commands.broker_state_dir.strip()) else None,
        broker_tray_launcher=Path(v) if (v := commands.broker_tray_launcher.strip()) else None,
        regen_media_root=Path(v) if (v := manifest.regen.media_root.strip()) else None,
        regen_metadata_root=Path(v) if (v := manifest.regen.metadata_root.strip()) else None,
        regen_generate_video_url=manifest.regen.generate_video_url,
        regen_generate_image_url=manifest.regen.generate_image_url,
        origenerator_enabled=bool(manifest.runtime.origenerator_dir.strip()),
        origenerator_cmd_file=Path(v) if (v := commands.origenerator_cmd_file.strip()) else None,
        origenerator_said_at_suffix=(
            said_at_suffix_of(v) if (v := manifest.runtime.origenerator_dir.strip()) else ""),
        origenerator_paused_file=Path(v) if (v := commands.origenerator_paused_file.strip()) else None,
        origenerator_status_file=Path(v) if (v := commands.origenerator_status_file.strip()) else None,
        portrait_origenerator_hud_file=(
            Path(v) if (v := commands.portrait_origenerator_hud_file.strip()) else None),
        landscape_origenerator_hud_file=(
            Path(v) if (v := commands.landscape_origenerator_hud_file.strip()) else None),
    )
