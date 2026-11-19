from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from app_support import osr2_device
from app_support.file_channel import read_flag
from funestra import broker
from funestra.file_channel import append_command
from funestra.main_hud import BROKER_PRESS
from shared_ui.message_shape import answering_what_he_said

from .bridge_records import BridgeConfig, Op, WindowOp
from .command_dispatch import app_at_defaults, dispatch_command
from .command_value import Command, parse_command
from .dashboard_actions import (
    BROWSE_LIBRARY_CLOSE,
    ENTER_OMNIPAUSE,
    HEADSET_OFF,
    HEADSET_SIGNALS,
    HELP_REFERENCE,
    HELP_REFERENCE_COMMANDS,
    LIBRARY_OPEN_FILENAME,
    ORIGENERATOR_ACTIVATE,
    REFERENCE_OPEN_FILENAME,
    RFB_ACTIVATE,
)
from .dashboard_bridge import DashboardSnapshot, DashboardSnapshotWriter, snapshot_word
from .device_arbiter import DeviceArbiter, who_drives_the_osr2
from .dispatch_loop_setup import SessionFiles
from .event_log import MESSAGE, SOURCE_SYSTEM, flash_message
from .funestra_status import read_main_funestra_status
from .genaumacher_save import save_and_say
from .headset_power import the_headset_is_ready
from .hosted_words import aimed_at_the_tracked_target, with_the_target_it_names
from .lock import core_goes_ahead_to
from .lock_follower import LockFollower, what_the_main_funestra_holds
from .loopback_inbox import PRESS_PORT_FILENAME, post_to_inbox
from .mode_plan import kino_shows
from .origenerator_opening import OrigeneratorOpening
from .runtime_flow import write_flag_file
from .session_end import mark_session_end
from .session_environment import ORDINARY_SESSION, SessionEnvironment
from .session_handoff import DESKTOP, VR, HandoffTarget, request_a_crossing
from .shared_state import BridgeState, write_shared_state
from .special_collections import Collection, every_funestra_keeps_only
from .voice_commands import CommandLine
from .voice_control import NOTHING_TO_ACCEPT, AppVoice
from .watch_sampling import WatchSampler
from .watch_stats import watch_stats_path
from .window_work import WINDOW_OPS, WindowWork

logger = logging.getLogger(__name__)

HANDOFF_COMMANDS: dict[str, HandoffTarget] = {"enter_vr": VR, "exit_vr": DESKTOP}
REQUEST_NEWS: dict[str, tuple[str, bool]] = {
    f"{satellite}_request_{news}": (satellite, opened)
    for satellite in ("portrait", "landscape")
    for news, opened in (("open", True), ("closed", False))
}

_DASHBOARD_WARNING_EVERY_S = 60.0


class CommandAnswering:
    def __init__(
        self,
        config: BridgeConfig,
        files: SessionFiles,
        *,
        state: BridgeState,
        window_work: WindowWork,
        opening: OrigeneratorOpening,
        dashboard_enabled: bool,
        env: SessionEnvironment = ORDINARY_SESSION,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.state = state
        self.window_work = window_work
        self.shared_state_file = files.shared_state
        self.ahk_cmd_file = files.ahk_cmd
        self.dashboard_enabled = dashboard_enabled
        self.env = env
        self.voice = AppVoice()
        self.watch = WatchSampler(funestras=config.funestras, genau=config.genau,
                                  stats_file=watch_stats_path(config.state_dir))
        self.arbiter = DeviceArbiter(
            funestras=config.funestras, genau=config.genau,
            device_on=lambda: osr2_device.switched_on(config.broker_state))
        self.lock_follower = LockFollower(what_the_main_funestra_holds(config))
        self._opening = opening
        self._clock = clock
        self._answers = 0
        self._ignored_because = ""
        self._last_dashboard_warning = float("-inf")
        self._dashboard_snapshot = DashboardSnapshotWriter(self.config.dashboard_state_file)

    def hear_as_a_lock(self, phrases: frozenset[str]) -> None:
        self.config = replace(self.config, origenerator_lock_phrases=phrases)

    def handle_line(self, line: CommandLine) -> None:
        if line.spoken_at is None:
            self._answer_the_line(line)
            return
        with answering_what_he_said():
            self._answer_the_line(line)

    def _answer_the_line(self, line: CommandLine) -> None:
        said = parse_command(line.command)
        if line.already_sent:
            self._what_its_funestra_already_did(said)
            return
        source = said.message_source(self.state.active_funestra)
        answers_before = self._answers
        self._ignored_because = ""
        for command in aimed_at_the_tracked_target(said, self.state).each():
            self.answer(command, line.spoken_at, pressed_at=line.pressed_at)
        if not line.said:
            return
        if self._ignored_because:
            self.flash(f"ignored {self._ignored_because}: {line.said}", source=source,
                       level=logging.WARNING)
        elif self._answers == answers_before:
            self.flash(line.said, source=source)

    def _what_its_funestra_already_did(self, command: Command) -> None:
        logger.info("Its Funestra already took %s", command.text)
        self.echo_the_press_best_effort(command.text)
        self.watch.note_command(command.text)
        self.keep(with_the_target_it_names(command, self.state))

    def flash(self, message: str, *, source: str, level: int = MESSAGE) -> None:
        self.an_answer_was_given()
        flash_message(logger, message, source=source, level=level)

    def an_answer_was_given(self) -> None:
        self._answers += 1

    def it_was_ignored_because(self, why: str) -> None:
        self._ignored_because = why

    def answer(self, command: str | Command, spoken_at: float | None = None, *,
               pressed_at: float | None = None) -> None:
        if isinstance(command, str):
            command = parse_command(command)
        if command.text in HEADSET_SIGNALS:
            _headset(self, command.text)
            return
        self.echo_the_press_best_effort(command.text)
        if command.text in (RFB_ACTIVATE, ORIGENERATOR_ACTIVATE):
            self._opening.he_picked_what_shows_in_the_browsers_place()
        its_own = LOOP_COMMANDS.get(command.text)
        if its_own is None:
            self.dispatch(command, spoken_at, pressed_at=pressed_at)
        else:
            its_own(self, spoken_at)

    def dispatch(self, command: Command, spoken_at: float | None = None, *,
                 pressed_at: float | None = None) -> None:
        logger.info("Dispatching command: %s", command.text)
        given_at = spoken_at if spoken_at is not None else pressed_at
        if self.watch.already_moved_on_by_itself(
                command, given_at, now=self._clock(),
                genau_on_screen=not kino_shows(self.state.main_mode)):
            logger.info("%s was pressed on a video already gone from the screen; "
                        "the move it asked for has happened", command.text)
            return
        target_path = self.watch.video_at(command, given_at)
        self.watch.note_command(command.text)
        self.take(*dispatch_command(
            command, self.state, self.config,
            target_path=target_path,
            given_at=given_at,
        ))

    def take(self, new_state: BridgeState, ops: list[WindowOp]) -> None:
        before, self.state = self.state, new_state
        if who_drives_the_osr2(before) != who_drives_the_osr2(new_state):
            self.arbiter.follow(new_state)
        for op in ops:
            handler = OP_HANDLERS.get(op.op)
            if handler is None:
                logger.error("unhandled window op %r", op.op)
                continue
            handler(self, op)
        self.window_work.raise_a_browse_the_restack_left_underneath()
        write_shared_state(self.shared_state_file, self.state)
        self.window_work.flush_rfb_tabs_unless_a_tick_holds_them(self.state)
        self.update_dashboard_if_there_is_one()

    def keep(self, state: BridgeState) -> None:
        self.state = state
        write_shared_state(self.shared_state_file, state)

    def echo_the_press_best_effort(self, action: str) -> None:
        if self.dashboard_enabled:
            post_to_inbox(self.config.state_dir / PRESS_PORT_FILENAME, action)

    def update_dashboard_if_there_is_one(self) -> None:
        if self.dashboard_enabled:
            self.update_dashboard()

    def update_dashboard(self) -> None:
        try:
            changed = self._dashboard_snapshot.write(
                DashboardSnapshot(
                    omni_paused=self.state.omni_paused,
                    voice_active=self.voice.active,
                    favorites=every_funestra_keeps_only(self.state, self.config,
                                                        Collection.FAVORITES),
                    in_vr=self.config.vr_main_funestra,
                    nothing_to_reset=app_at_defaults(
                        self.state, self.config,
                        read_main_funestra_status(self.config.main_funestra.status_file)),
                    headset_ready=the_headset_is_ready(self.config.state_dir),
                    origenerator_core_ready=self.state.origenerator_core_ready,
                    rfb_mode=self.state.rfb_mode,
                ),
            )
        except OSError as exc:
            now = self._clock()
            if now - self._last_dashboard_warning >= _DASHBOARD_WARNING_EVERY_S:
                self._last_dashboard_warning = now
                logger.warning("dashboard snapshot write failed: %s", exc)
            return
        if changed:
            self.echo_the_press_best_effort(snapshot_word(self._dashboard_snapshot.last_written))

    def flag_the_panel_for_the_headset(self, filename: str, *, toggling: bool) -> None:
        path = Path(self.config.state_dir) / filename
        write_flag_file(path, toggling and not read_flag(path, default=False))

    def log_any_window_still_on_top_under_the_pause(self) -> None:
        self.window_work.log_topmost_state("post-enter", self.state)


def _the_dashboard_asks_first(answering: CommandAnswering) -> bool:
    return answering.dashboard_enabled and not answering.config.vr_main_funestra


def _quit(answering: CommandAnswering, spoken_at: float | None) -> None:
    if _the_dashboard_asks_first(answering):
        return
    _begin_an_ending_esc_can_call_off(
        answering,
        "the quit command, spoken" if spoken_at is not None else "the quit command, pressed",
    )


def _begin_an_ending_esc_can_call_off(answering: CommandAnswering, asked_by: str) -> None:
    mark_session_end(answering.config.state_dir, asked_by)
    answering.ahk_cmd_file.write_text("end_session", encoding="utf-8")


def _cross(target: HandoffTarget, answering: CommandAnswering,
           _spoken_at: float | None) -> None:
    refused = request_a_crossing(target, answering.config.state_dir,
                                 vr_main_funestra=answering.config.vr_main_funestra)
    if refused:
        answering.flash(refused, source=SOURCE_SYSTEM, level=logging.WARNING)
        return
    _begin_an_ending_esc_can_call_off(answering, f"a crossing to {target.app_name}")


def _show_the_reference(toggling: bool, answering: CommandAnswering,
                        _spoken_at: float | None) -> None:
    answering.flag_the_panel_for_the_headset(REFERENCE_OPEN_FILENAME, toggling=toggling)


def _omniminimize(answering: CommandAnswering, _spoken_at: float | None) -> None:
    answering.window_work.minimize_all(answering.state)


def _omnirestore(answering: CommandAnswering, _spoken_at: float | None) -> None:
    answering.window_work.windows.restore_minimized()


def _toggle_omnipause(answering: CommandAnswering, spoken_at: float | None) -> None:
    if not answering.state.omni_paused:
        _enter_omnipause("omnipause_toggle", answering, spoken_at)
        return
    answering.dispatch(parse_command("omnipause_toggle"))
    answering.window_work.log_topmost_state("post-leave", answering.state)


def _enter_omnipause(command: str, answering: CommandAnswering,
                     _spoken_at: float | None) -> None:
    answering.dispatch(parse_command(command))
    answering.log_any_window_still_on_top_under_the_pause()


def _enter_omnipause_unless_paused(answering: CommandAnswering,
                                   spoken_at: float | None) -> None:
    if not answering.state.omni_paused:
        _enter_omnipause(ENTER_OMNIPAUSE, answering, spoken_at)


def _relieve_even_under_a_pause(answering: CommandAnswering, spoken_at: float | None) -> None:
    _enter_omnipause("relief_omnipause", answering, spoken_at)


def _pause(answering: CommandAnswering, spoken_at: float | None) -> None:
    if not answering.state.omni_paused:
        _toggle_omnipause(answering, spoken_at)


def _play(answering: CommandAnswering, spoken_at: float | None) -> None:
    if answering.state.omni_paused:
        _toggle_omnipause(answering, spoken_at)


def _browse_the_library(answering: CommandAnswering, _spoken_at: float | None) -> None:
    if answering.config.vr_main_funestra:
        answering.flag_the_panel_for_the_headset(LIBRARY_OPEN_FILENAME, toggling=True)
    else:
        threading.Thread(
            target=answering.window_work.browse_library,
            args=(lambda: answering.state,),
            daemon=True,
            name="library-browser",
        ).start()


def _close_the_browse(answering: CommandAnswering, _spoken_at: float | None) -> None:
    answering.flag_the_panel_for_the_headset(LIBRARY_OPEN_FILENAME, toggling=False)


def _press_the_broker(answering: CommandAnswering, _spoken_at: float | None) -> None:
    broker.press(answering.config.broker_state, answering.config.broker_tray_launcher)


def _start_the_broker(answering: CommandAnswering, _spoken_at: float | None) -> None:
    if not broker.is_running(answering.config.broker_state):
        broker.start(answering.config.broker_tray_launcher)


def _stop_the_broker(answering: CommandAnswering, _spoken_at: float | None) -> None:
    if broker.is_running(answering.config.broker_state):
        broker.stop(answering.config.broker_state, answering.config.broker_tray_launcher)


def _toggle_voice(command: str, answering: CommandAnswering, _spoken_at: float | None) -> None:
    muted = answering.voice.toggle(command)
    if muted is None:
        return
    keeping_the_mute_for_the_next_sessions_listener = replace(answering.state, mic_muted=muted)
    answering.keep(keeping_the_mute_for_the_next_sessions_listener)
    answering.update_dashboard_if_there_is_one()


def _accept_what_voice_was_not_sure_of(answering: CommandAnswering,
                                       _spoken_at: float | None) -> None:
    if not answering.voice.accept():
        answering.flash(NOTHING_TO_ACCEPT, source=SOURCE_SYSTEM, level=logging.WARNING)


def _pass_on_request_news(satellite: str, opened: bool, answering: CommandAnswering,
                          _spoken_at: float | None) -> None:
    answering.voice.request_news(satellite, opened)


def _headset(answering: CommandAnswering, signal: str) -> None:
    off = signal == HEADSET_OFF
    if not answering.voice.headset_is(off=off):
        return
    logger.info("The headset is %s", "off: only quit is heard" if off else "back on")
    append_command(answering.ahk_cmd_file, signal)
    answering.voice.suspend_as_the_app_is(paused=answering.state.omni_paused)


LOOP_COMMANDS: dict[str, Callable[[CommandAnswering, float | None], None]] = {
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


AHK_PASSTHROUGH_OPS = frozenset({Op.SUSPEND_HOTKEYS, Op.UNSUSPEND_HOTKEYS})


def _run_window_work(answering: CommandAnswering, op: WindowOp) -> None:
    answering.window_work.run(op, answering.state)


def _run_save_video_on_a_worker(answering: CommandAnswering, _op: WindowOp) -> None:
    answering.an_answer_was_given()
    threading.Thread(
        target=save_and_say,
        args=(answering.config,),
        daemon=True,
        name="genaumacher-save",
    ).start()


def _run_an_answer_given_elsewhere(answering: CommandAnswering, _op: WindowOp) -> None:
    answering.an_answer_was_given()


def _run_ignored(answering: CommandAnswering, op: WindowOp) -> None:
    answering.it_was_ignored_because(op.key)


def _run_notice(answering: CommandAnswering, op: WindowOp) -> None:
    answering.flash(op.key, source=op.source, level=op.level)


def _run_follow_the_lock(answering: CommandAnswering, op: WindowOp) -> None:
    shown = answering.lock_follower.follow(op.key)
    if core_goes_ahead_to(shown, answering.state, answering.config):
        answering.lock_follower.core_went_ahead_to(shown)


def _run_ahk_passthrough(answering: CommandAnswering, op: WindowOp) -> None:
    if op.op == Op.UNSUSPEND_HOTKEYS and answering.env.integration:
        return
    append_command(answering.ahk_cmd_file, op.op)


OP_HANDLERS: dict[str, Callable[[CommandAnswering, WindowOp], None]] = {
    **dict.fromkeys(WINDOW_OPS, _run_window_work),
    Op.MESSAGE: _run_notice,
    **dict.fromkeys(AHK_PASSTHROUGH_OPS, _run_ahk_passthrough),
    Op.SAVE_CLIP: _run_save_video_on_a_worker,
    Op.MAIN_FUNESTRA_ANSWERS: _run_an_answer_given_elsewhere,
    Op.NARROWING_ANSWERS: _run_an_answer_given_elsewhere,
    Op.IGNORED: _run_ignored,
    Op.FOLLOW_THE_LOCK: _run_follow_the_lock,
}
assert set(OP_HANDLERS) == set(Op), "every window op needs a handler"
