from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from app_support import osr2_device, wrong_actions
from app_support.file_channel import consume_command_file, read_flag
from app_support.threading_utils import start_daemon_thread
from app_support.win32 import force_foreground_window, window_exists
from funestra import broker
from funestra.file_channel import append_command
from funestra.funestra_verbs import play_file
from funestra.main_hud import BROKER_PRESS
from funestra.playlist import PlaylistItem

from .arrivals import keep_looking
from .bridge_records import BridgeConfig, Op, WindowOp
from .child_launch import no_child_log
from .command_dispatch import app_at_defaults, dispatch_command
from .command_value import Command, parse_command
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
from .device_arbiter import DeviceArbiter, FunestraFiles, who_drives_the_osr2
from .dispatch_loop_setup import LOOP_TICK_S, Publishing, SessionFiles, SessionStart
from .event_log import MESSAGE, SOURCE_SYSTEM, flash_message
from .f_mode import every_funestra_in_f_mode
from .fun_time_funestras import FunTimeFunestra
from .funestra_handover import Departures, Homecoming, SatellitesChangingHands
from .funestra_messages import FunestraMessages
from .funestra_settings import FunestraReporting
from .funestra_status import read_genau_status, read_main_funestra_status
from .genaumacher_save import save_and_say
from .headset_power import the_headset_is_ready
from .hosted_contract import said_at_suffix_of
from .hud_feed import HudFeed
from .latest_arrivals import LatestArrivals
from .library_browser import BrowseOpening, browse_library
from .lock import a_lock_holds
from .lock_follower import LockFollower, what_the_main_funestra_holds
from .loopback_inbox import PRESS_PORT_FILENAME, post_to_inbox
from .main_list_builds import BuildsOffTheLoop
from .manifest import WINDOWS_BRIDGE_MANIFEST_FILENAME, LaunchManifest
from .mode_plan import kino_shows
from .modes import scripted_item
from .origenerator_opening import OrigeneratorOpening, holding_nothing
from .origenerator_switches import RfbMode
from .runtime_flow import write_flag_file
from .satellite_speeds import SatelliteSpeeds
from .session_end import mark_session_end
from .session_environment import ORDINARY_SESSION, SessionEnvironment
from .session_handoff import DESKTOP, VR, HandoffTarget, request_a_crossing
from .shared_state import BridgeState, read_shared_state, write_shared_state
from .shortcuts import Shortcut
from .text_field_clicks import TypingWatch
from .voice_commands import CommandLine, parse_command_line
from .voice_control import NOTHING_TO_ACCEPT, AppVoice
from .watch_sampling import WatchSampler
from .watch_stats import watch_stats_path
from .win32 import find_window_for_process, set_always_on_top, window_rect
from .window_names import visible_windows
from .windows_bridge_random_faves_browser import open_rfb_tab

logger = logging.getLogger(__name__)


def raise_a_browse_the_restack_left_underneath(browsing: subprocess.Popen | None) -> int:
    if browsing is None:
        return 0
    its_pid_may_be_another_programs_by_now = browsing.poll() is not None
    if its_pid_may_be_another_programs_by_now:
        return 0
    hwnd = find_window_for_process(browsing.pid)
    if hwnd:
        set_always_on_top(hwnd, True)
    return hwnd


def poll_dashboard_commands(cmd_file: Path) -> list[str]:
    return consume_command_file(cmd_file, uppercase=False)


HANDOFF_COMMANDS: dict[str, HandoffTarget] = {"enter_vr": VR, "exit_vr": DESKTOP}
REQUEST_NEWS: dict[str, tuple[str, bool]] = {
    f"{satellite}_request_{news}": (satellite, opened)
    for satellite in ("portrait", "landscape")
    for news, opened in (("open", True), ("closed", False))
}


LONGER_THAN_A_STALLED_DISK_HOLDS_A_TICK_S = 90.0


def detect_sleep_gap(prev_wall: float, now_wall: float, *,
                     threshold_s: float = LONGER_THAN_A_STALLED_DISK_HOLDS_A_TICK_S) -> float | None:
    # The thread freezes while Windows sleeps, and AHK's hotkeys are prone to not firing after a
    # wake.
    gap = now_wall - prev_wall
    return gap if gap >= threshold_s else None


_DASHBOARD_WARNING_EVERY_S = 60.0


class DispatchLoopRunner:
    def __init__(
        self,
        config: BridgeConfig,
        files: SessionFiles,
        start: SessionStart,
        publishing: Publishing,
        env: SessionEnvironment = ORDINARY_SESSION,
    ) -> None:
        self.config = config
        self.dashboard_cmd_file = files.dashboard_cmd
        self.shared_state_file = files.shared_state
        self.ahk_cmd_file = files.ahk_cmd
        self.manifest_path = files.manifest or (
            config.state_dir / WINDOWS_BRIDGE_MANIFEST_FILENAME
        )
        self.windows = start.windows
        self.seating = Seating(publishing.secondary_rects,
                               main_funestra_status_file=config.main_funestra_status_file,
                               genau_status_file=config.genau_status_file)
        self.dashboard_enabled = publishing.dashboard_enabled
        self.env = env
        self.hud = HudFeed(config=config, publisher=publishing.hud_publisher)
        self.rfb_shortcut = start.rfb_shortcut or Shortcut()
        self.rfb_slideshow = start.rfb_slideshow
        self.typing = TypingWatch(start.press_and_caret_watch, typed_into=lambda seen: self.windows.takes_typing(
            seen.window, seen.process, hosted_app=self.config.origenerator_enabled))
        self.sync_interval_s = publishing.sync_interval_ms / 1000
        left_in = read_shared_state(files.shared_state) or BridgeState()
        self.state = holding_nothing(left_in)
        self.opening = OrigeneratorOpening(
            config, left_in, already_open=start.origenerator_already_open,
            slideshow_on=start.origenerator_slideshow_on)
        self._last_sync = 0.0
        self._last_dashboard_warning = float("-inf")
        self._stop = threading.Event()
        self._browse_lock = threading.Lock()
        self._browser_process: subprocess.Popen | None = None
        self._pending_rfb_urls: list[str] = []
        self._holding_a_ticks_tabs_for_one_chrome_launch = False
        self.messages = FunestraMessages(config)
        self.reporting = FunestraReporting()
        self.voice = AppVoice()
        self._answers = 0
        self._ignored_because = ""
        self.homecoming = Homecoming()
        self.departures = Departures()
        self.changing_hands = SatellitesChangingHands(
            self.departures, self.homecoming,
            left_on=self.hud.the_video_it_comes_home_to,
            changed=partial(_publish_the_satellite_changing_hands, self))
        self.watch = WatchSampler(
            main_funestra_status_file=config.main_funestra_status_file,
            satellite_status_files={funestra: config.satellite(funestra).status_file
                                    for funestra in FunTimeFunestra.SATELLITES},
            stats_file=watch_stats_path(config.state_dir),
        )
        self.arbiter = DeviceArbiter(
            main_funestra=FunestraFiles(status_file=config.main_funestra_status_file,
                                        cmd_file=config.main_funestra_cmd_file),
            genau_cmd_file=config.genau_cmd_file,
            device_on=lambda: osr2_device.switched_on(config.broker_state),
            satellites={funestra: FunestraFiles(status_file=config.satellite(funestra).status_file,
                                              cmd_file=config.satellite(funestra).cmd_file)
                        for funestra in FunTimeFunestra.SATELLITES},
        )
        self.satellite_speeds = SatelliteSpeeds(
            main_funestra_status_file=config.main_funestra_status_file,
            satellite_cmd_files=tuple(
                config.satellite(funestra).cmd_file for funestra in FunTimeFunestra.SATELLITES),
        )
        self.lock_follower = LockFollower(what_the_main_funestra_holds(config))
        self.latest_arrivals = LatestArrivals(config)

    def activate_origenerator(self, *, wait_for_the_app) -> None:
        owed = self.opening.take_what_is_owed(wait_for_the_app)
        if owed:
            self.state = self.opening.app_as_it_is(self.state)
        for command in owed:
            self._dispatch(parse_command(command))

    def tick(self) -> None:
        self.messages.flash_new()

        shared = read_shared_state(self.shared_state_file)
        if shared is not None:
            self.state = shared
        self.state = self.opening.app_as_it_is(self.state)
        self.state = self.reporting.recorded(self.state, self.config, now=time.monotonic())
        if shared is not None and shared != self.state:
            write_shared_state(self.shared_state_file, self.state)

        self.arbiter.follow(self.state)

        self._holding_a_ticks_tabs_for_one_chrome_launch = True
        try:
            for line in poll_dashboard_commands(self.dashboard_cmd_file):
                self._handle_line(parse_command_line(line))
        finally:
            self._holding_a_ticks_tabs_for_one_chrome_launch = False
        self._pause_for_a_click_into_a_text_field()
        self._flush_rfb_tabs()
        self.windows.flush_pending_hides()
        seated = self.seating.seat(self.state, self.windows)
        if seated is not self.state:
            self.state = seated
            write_shared_state(self.shared_state_file, self.state)

        self.voice.suspend_as_the_app_is(paused=self.state.omni_paused)

        now = time.monotonic()
        if now - self._last_sync >= self.sync_interval_s:
            self._last_sync = now
            self._converge_origenerator_window()
            self.satellite_speeds.take_the_main_funestras_rate()
            if self.dashboard_enabled:
                self._update_dashboard()
        self.state = self.changing_hands.follow(self.state, self.config, now=now)
        self.latest_arrivals.deliver(self.state, coming_home=self.homecoming.funestras)
        held = self.lock_follower.held()
        if held:
            self._take(*a_lock_holds(held, self.state, self.config))
        self.watch.sample_due(now=now, omni_paused=self.state.omni_paused)
        self.hud.publish_due(self.state, now=now)
        if self.rfb_slideshow is not None:
            self.rfb_slideshow.tick(now=now, held=self.state.omni_paused
                                    or self.state.rfb_mode is RfbMode.ORIGENERATOR)

    def _pause_for_a_click_into_a_text_field(self) -> None:
        if not self.typing.clicked_into_a_text_field(now=time.monotonic()):
            return
        if not self.state.omni_paused:
            logger.info("A click into a text field enters OmniPause")
            self._handle_command(ENTER_OMNIPAUSE)

    def hear_as_a_lock(self, phrases: frozenset[str]) -> None:
        self.config = replace(self.config, origenerator_lock_phrases=phrases)

    def _handle_line(self, line: CommandLine) -> None:
        said = parse_command(line.command)
        source = said.message_source(self.state.active_funestra)
        answers_before = self._answers
        self._ignored_because = ""
        for command in said.aimed_at_the_active(self.state.active_funestra).each():
            self._handle_command(command, line.spoken_at)
        if not line.said:
            return
        if self._ignored_because:
            self._flash(f"ignored {self._ignored_because}: {line.said}", source=source,
                        level=logging.WARNING)
        elif self._answers == answers_before:
            self._flash(line.said, source=source)

    def _flash(self, message: str, *, source: str, level: int = MESSAGE) -> None:
        self._answers += 1
        flash_message(logger, message, source=source, level=level)

    def _handle_command(self, command: str | Command, spoken_at: float | None = None) -> None:
        if isinstance(command, str):
            command = parse_command(command)
        if command.text in HEADSET_SIGNALS:
            _headset(self, command.text)
            return
        self._echo_the_press_best_effort(command.text)
        answer = _LOOP_COMMANDS.get(command.text)
        if answer is None:
            self._dispatch(command, spoken_at)
        else:
            answer(self, spoken_at)

    def _dispatch(self, command: Command, spoken_at: float | None = None) -> None:
        logger.info("Dispatching command: %s", command.text)
        self.watch.note_command(command.text)
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
                logger.error("unhandled window op %r", op.op)
                continue
            handler(self, op)
        raise_a_browse_the_restack_left_underneath(self._browser_process)
        write_shared_state(self.shared_state_file, self.state)
        if not self._holding_a_ticks_tabs_for_one_chrome_launch:
            self._flush_rfb_tabs()
        if self.dashboard_enabled:
            self._update_dashboard()

    def _flush_rfb_tabs(self) -> None:
        # Chrome's FindTabbedBrowser walks the profile's windows most-recently-active first, so the
        # RFB window is activated first.
        if self.state.rfb_mode is RfbMode.ORIGENERATOR:
            return
        urls = self._pending_rfb_urls
        self._pending_rfb_urls = []
        if not urls or not self.rfb_shortcut.target:
            return
        no_window_of_our_own = not window_exists(self.windows.rfb_hwnd)
        if no_window_of_our_own:
            logger.warning(
                "%d RFB tab(s) skipped: no Random Faves Browser window to open into", len(urls))
            return
        if not force_foreground_window(self.windows.rfb_hwnd):
            logger.info("RFB window did not take the foreground before the tab handoff")
        open_rfb_tab(urls=urls, shortcut=self.rfb_shortcut)
        logger.info("Opened %d RFB tab(s)", len(urls))
        if self.rfb_slideshow is not None:
            self.rfb_slideshow.restart(now=time.monotonic())

    def _echo_the_press_best_effort(self, action: str) -> None:
        if self.dashboard_enabled:
            post_to_inbox(self.config.state_dir / PRESS_PORT_FILENAME, action)

    def _update_dashboard(self) -> None:
        try:
            write_dashboard_snapshot(
                str(self.config.dashboard_state_file),
                DashboardSnapshot(
                    omni_paused=self.state.omni_paused,
                    voice_active=self.voice.active,
                    f_mode=every_funestra_in_f_mode(self.state, self.config),
                    in_vr=self.config.vr_main_funestra,
                    nothing_to_reset=app_at_defaults(
                        self.state, self.config,
                        read_main_funestra_status(self.config.main_funestra_status_file)),
                    headset_ready=the_headset_is_ready(self.config.state_dir),
                    origenerator_ready=(self.config.origenerator_enabled
                                        and self.state.origenerator_ready),
                    rfb_mode=self.state.rfb_mode,
                ),
            )
        except OSError as exc:
            now = time.monotonic()
            if now - self._last_dashboard_warning >= _DASHBOARD_WARNING_EVERY_S:
                self._last_dashboard_warning = now
                logger.warning("dashboard snapshot write failed: %s", exc)

    def _converge_origenerator_window(self) -> None:
        the_pause_put_every_window_where_it_stands = self.state.omni_paused
        if the_pause_put_every_window_where_it_stands:
            return
        self.windows.converge_origenerator_window(self.state.rfb_mode)

    def _flag_the_panel_for_the_headset(self, filename: str, *, toggling: bool) -> None:
        path = Path(self.config.state_dir) / filename
        write_flag_file(path, toggling and not read_flag(path, default=False))

    def _log_which_windows_missed_their_startup_promotion(self) -> None:
        self._log_topmost_state("startup")

    def _log_any_window_still_on_top_under_the_pause(self) -> None:
        self._log_topmost_state("post-enter")

    def _log_topmost_state(self, label: str) -> None:
        logger.info("Topmost [%s] mode=%s: %s", label, self.state.main_mode,
                    self.windows.topmost_report())

    def _handle_browse_library(self) -> None:
        if not self._browse_lock.acquire(blocking=False):
            return
        try:
            self._browse_library_inner()
        finally:
            self._browse_lock.release()

    def _browse_library_inner(self) -> None:
        omnipause_already_holds_them_down = self.state.omni_paused

        if not omnipause_already_holds_them_down:
            self.windows.remove_all_topmost()
            self._stop_the_hotkeys_eating_the_browsers_keys()

        try:
            genau = not kino_shows(self.state.main_mode)
            where_the_pick_will_play = self.windows.hwnd("main_funestra")
            selected = browse_library(
                self.manifest_path,
                self.config.python_exe,
                BrowseOpening(
                    over=window_rect(where_the_pick_will_play) if where_the_pick_will_play else None,
                    playing=(read_genau_status(self.config.genau_status_file).flick if genau
                             else read_main_funestra_status(self.config.main_funestra_status_file).video),
                    flicks=genau,
                ),
                runner=self._run_browser,
            )
            if selected and genau:
                append_command(self.config.genau_cmd_file, play_file(PlaylistItem(Path(selected))))
            elif selected:
                append_command(self.config.main_funestra_cmd_file, play_file(scripted_item(selected, self.config.regen_metadata_root)))
        finally:
            if not omnipause_already_holds_them_down:
                self.windows.restore_all_topmost(self.state.rfb_mode)
                append_command(self.ahk_cmd_file, Op.UNSUSPEND_HOTKEYS)

    def _stop_the_hotkeys_eating_the_browsers_keys(self) -> None:
        append_command(self.ahk_cmd_file, Op.SUSPEND_HOTKEYS)

    def run(self) -> None:
        self._log_which_windows_missed_their_startup_promotion()
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
            self._stop.wait(LOOP_TICK_S)

    def _run_browser(self, command, **kwargs) -> None:
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


def _quit(runner: DispatchLoopRunner, spoken_at: float | None) -> None:
    _begin_an_ending_esc_can_call_off(
        runner,
        "the quit command, spoken" if spoken_at is not None else "the quit command, pressed",
    )


def _begin_an_ending_esc_can_call_off(runner: DispatchLoopRunner, asked_by: str) -> None:
    mark_session_end(runner.config.state_dir, asked_by)
    runner.ahk_cmd_file.write_text("end_session", encoding="utf-8")


def _cross(target: HandoffTarget, runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    refused = request_a_crossing(target, runner.config.state_dir,
                                 vr_main_funestra=runner.config.vr_main_funestra)
    if refused:
        runner._flash(refused, source=SOURCE_SYSTEM, level=logging.WARNING)
        return
    _begin_an_ending_esc_can_call_off(runner, f"a crossing to {target.app_name}")


def _show_the_reference(toggling: bool, runner: DispatchLoopRunner,
                        _spoken_at: float | None) -> None:
    runner._flag_the_panel_for_the_headset(REFERENCE_OPEN_FILENAME, toggling=toggling)


def _omniminimize(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    runner.windows.minimize_all(visible_windows(runner.state.rfb_mode))


def _omnirestore(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    runner.windows.restore_minimized()


def _toggle_omnipause(runner: DispatchLoopRunner, spoken_at: float | None) -> None:
    if not runner.state.omni_paused:
        _enter_omnipause("omnipause_toggle", runner, spoken_at)
        return
    runner._dispatch(parse_command("omnipause_toggle"))
    runner._log_topmost_state("post-leave")


def _enter_omnipause(command: str, runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    runner._dispatch(parse_command(command))
    runner._log_any_window_still_on_top_under_the_pause()


def _enter_omnipause_unless_paused(runner: DispatchLoopRunner, spoken_at: float | None) -> None:
    if not runner.state.omni_paused:
        _enter_omnipause(ENTER_OMNIPAUSE, runner, spoken_at)


def _relieve_even_under_a_pause(runner: DispatchLoopRunner, spoken_at: float | None) -> None:
    _enter_omnipause("relief_omnipause", runner, spoken_at)


def _pause(runner: DispatchLoopRunner, spoken_at: float | None) -> None:
    if not runner.state.omni_paused:
        _toggle_omnipause(runner, spoken_at)


def _play(runner: DispatchLoopRunner, spoken_at: float | None) -> None:
    if runner.state.omni_paused:
        _toggle_omnipause(runner, spoken_at)


def _browse_the_library(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    if runner.config.vr_main_funestra:
        runner._flag_the_panel_for_the_headset(LIBRARY_OPEN_FILENAME, toggling=True)
    else:
        threading.Thread(
            target=runner._handle_browse_library,
            daemon=True,
            name="library-browser",
        ).start()


def _close_the_browse(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    runner._flag_the_panel_for_the_headset(LIBRARY_OPEN_FILENAME, toggling=False)


def _press_the_broker(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    broker.press(runner.config.broker_state, runner.config.broker_tray_launcher)


def _start_the_broker(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    if not broker.is_running(runner.config.broker_state):
        broker.start(runner.config.broker_tray_launcher)


def _stop_the_broker(runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    if broker.is_running(runner.config.broker_state):
        broker.stop(runner.config.broker_state, runner.config.broker_tray_launcher)


def _toggle_voice(command: str, runner: DispatchLoopRunner, _spoken_at: float | None) -> None:
    muted = runner.voice.toggle(command)
    if muted is None:
        return
    _keep_the_mute_for_the_next_sessions_listener(runner, muted)
    if runner.dashboard_enabled:
        runner._update_dashboard()


def _keep_the_mute_for_the_next_sessions_listener(runner: DispatchLoopRunner, muted: bool) -> None:
    runner.state = replace(runner.state, mic_muted=muted)
    write_shared_state(runner.shared_state_file, runner.state)


def _accept_what_voice_was_not_sure_of(runner: DispatchLoopRunner,
                                       _spoken_at: float | None) -> None:
    if not runner.voice.accept():
        runner._flash(NOTHING_TO_ACCEPT, source=SOURCE_SYSTEM, level=logging.WARNING)


def _pass_on_request_news(satellite: str, opened: bool, runner: DispatchLoopRunner,
                          _spoken_at: float | None) -> None:
    runner.voice.request_news(satellite, opened)


def _headset(runner: DispatchLoopRunner, signal: str) -> None:
    off = signal == HEADSET_OFF
    if not runner.voice.headset_is(off=off):
        return
    logger.info("The headset is %s", "off: only quit is heard" if off else "back on")
    append_command(runner.ahk_cmd_file, signal)
    runner.voice.suspend_as_the_app_is(paused=runner.state.omni_paused)


_LOOP_COMMANDS: dict[str, Callable[[DispatchLoopRunner, float | None], None]] = {
    "quit": _quit,
    **{command: partial(_cross, target) for command, target in HANDOFF_COMMANDS.items()},
    **{command: partial(_show_the_reference, command == HELP_REFERENCE)
       for command in HELP_REFERENCE_COMMANDS},
    "omniminimize": _omniminimize,
    "omnirestore": _omnirestore,
    "omnipause_toggle": _toggle_omnipause,
    ENTER_OMNIPAUSE: _enter_omnipause_unless_paused,
    "relief_omnipause": _relieve_even_under_a_pause,
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
    **{command: partial(_pass_on_request_news, satellite, opened)
       for command, (satellite, opened) in REQUEST_NEWS.items()},
}


_AHK_PASSTHROUGH_OPS = {Op.SUSPEND_HOTKEYS, Op.UNSUSPEND_HOTKEYS}


def _run_show_window(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner.windows.show(op.key)


def _run_hide_window(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner.windows.hide_after_settle(op.key)


def _run_minimize_window(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner.windows.park(op.key)


def _run_restore_parked(runner: DispatchLoopRunner, _op: WindowOp) -> None:
    runner.windows.restore_parked()


def _run_activate_window(runner: DispatchLoopRunner, op: WindowOp) -> None:
    if not runner.env.integration:
        runner.windows.activate(op.key)


def _run_restack_rfb(runner: DispatchLoopRunner, _op: WindowOp) -> None:
    runner.windows.restack_rfb_mode(runner.state.rfb_mode, paused=runner.state.omni_paused)


def _run_disable_all_topmost(runner: DispatchLoopRunner, _op: WindowOp) -> None:
    runner.windows.remove_all_topmost()


def _run_restore_all_topmost(runner: DispatchLoopRunner, _op: WindowOp) -> None:
    runner.windows.restore_all_topmost(runner.state.rfb_mode)


def _run_open_rfb_tab(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner._pending_rfb_urls.append(op.key)


def _run_save_video_on_a_worker(runner: DispatchLoopRunner, _op: WindowOp) -> None:
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


def _publish_the_satellite_changing_hands(runner: DispatchLoopRunner, state: BridgeState) -> None:
    write_shared_state(runner.shared_state_file, state)
    runner.hud.publish(state)


def _run_hand_over_funestras(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner.departures.expect((FunTimeFunestra[op.key.upper()],), runner.config)


def _run_take_back_funestras(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner.homecoming.expect((FunTimeFunestra[op.key.upper()],), runner.config,
                             now=time.monotonic())


def _run_follow_the_lock(runner: DispatchLoopRunner, op: WindowOp) -> None:
    runner.lock_follower.follow(op.key)


def _run_ahk_passthrough(runner: DispatchLoopRunner, op: WindowOp) -> None:
    if op.op == Op.UNSUSPEND_HOTKEYS and runner.env.integration:
        return
    append_command(runner.ahk_cmd_file, op.op)


_OP_HANDLERS = {
    Op.MESSAGE: _run_notice,
    Op.SHOW_WINDOW: _run_show_window,
    Op.HIDE_WINDOW: _run_hide_window,
    Op.ACTIVATE_WINDOW: _run_activate_window,
    Op.MINIMIZE_WINDOW: _run_minimize_window,
    Op.RESTORE_PARKED: _run_restore_parked,
    Op.RESTACK_RFB: _run_restack_rfb,
    Op.DISABLE_ALL_TOPMOST: _run_disable_all_topmost,
    Op.RESTORE_ALL_TOPMOST: _run_restore_all_topmost,
    Op.SUSPEND_HOTKEYS: _run_ahk_passthrough,
    Op.UNSUSPEND_HOTKEYS: _run_ahk_passthrough,
    Op.OPEN_RFB_TAB: _run_open_rfb_tab,
    Op.SAVE_CLIP: _run_save_video_on_a_worker,
    Op.MAIN_FUNESTRA_ANSWERS: _run_an_answer_given_elsewhere,
    Op.NARROWING_ANSWERS: _run_an_answer_given_elsewhere,
    Op.IGNORED: _run_ignored,
    Op.HAND_OVER_FUNESTRAS: _run_hand_over_funestras,
    Op.TAKE_BACK_FUNESTRAS: _run_take_back_funestras,
    Op.FOLLOW_THE_LOCK: _run_follow_the_lock,
}
assert set(_OP_HANDLERS) == set(Op), "every window op needs a handler"


def build_bridge_config_from_manifest(
    manifest: LaunchManifest,
    *,
    vr_main_funestra: bool = False,
) -> BridgeConfig:
    commands = manifest.commands
    return BridgeConfig(
        vr_main_funestra=vr_main_funestra,
        main_list_builds=BuildsOffTheLoop(),
        portrait=manifest.portrait,
        landscape=manifest.landscape,
        trash_dir=Path(manifest.media.trash_dir),
        state_dir=Path(commands.state_dir),
        loopback_port=manifest.loopback_port,
        main_sources=manifest.media.main_funestra_library_sources,
        vr_library_dirs=manifest.media.vr_library_dirs,
        genau_vr_flicks=manifest.media.genau_vr_flicks,
        python_exe=manifest.executables.python_exe,
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
        wrong_actions_said_in=wrong_actions.origenerators_state(),
    )
