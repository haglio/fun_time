from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

from funestra.file_channel import append_command
from funestra.funestra_verbs import (
    JUMP_TO_FUNSCRIPT,
    LOCK_OFF,
    LOCK_ON,
    NEXT,
    NEXT_FUNSCRIPTED,
    PREV,
    SEEK_BACK,
    SEEK_FWD,
    TOGGLE_LOCK,
    TRASH,
    play_file,
)
from funestra.hud_controls import why_it_cannot_step
from funestra.hud_kinds import VIDEO

from .bridge_records import IN_GENAU_MODE, OUTSIDE_VR, BridgeConfig, Handler, Op, WindowOp, ignored
from .command_value import FUNESTRA_SOURCES
from .event_log import SOURCE_MAIN
from .fun_time_funestras import FunTimeFunestra
from .funestra_status import read_genau_status, read_main_funestra_status
from .hosted_contract import written_for_the_hosted_app
from .lock import SENT_TO_TRASH, UNFAVORITED, unfavorited
from .mode_plan import MAIN_GENAU_MODE, kino_shows
from .modes import same_file_to_windows, scripted_item
from .origenerator_trash import trash_file_line
from .osr2_commands import FUNSCRIPT_MOVES, skip_ahead_refused
from .satellite_control import read_scripted_status
from .shared_state import BridgeState
from .vr_main_funestra_verbs import AIMED_AT_A_VIDEO, VR_MAIN_FUNESTRA_VERBS

logger = logging.getLogger(__name__)


PLAYHEAD_ACTS = {
    "nudge_prev": SEEK_BACK,
    "nudge_next": SEEK_FWD,
}


MAIN_FUNESTRA_CMD_MAP = {
    "main_funestra_record_down": "RECORD_DOWN",
    "main_funestra_record_up": "RECORD_UP",
    "main_funestra_record_tap": "RECORD_TAP",
    "main_funestra_snippet_cancel": "SNIPPET_CANCEL",
    "main_funestra_cycle_version": "CYCLE_VERSION",
    "main_funestra_cycle_version_back": "CYCLE_VERSION_BACK",
    "main_funestra_compilation": "PLAY_COMPILATION",
    "main_funestra_end_compilation": "END_COMPILATION",
    "main_funestra_full_vid": "PLAY_FULL_VID",
    "main_funestra_clip_jump": "PLAY_CLIP_JUMP",
    **{f"main_funestra_{act}": verb for act, verb in FUNSCRIPT_MOVES.items()},
}


_VERBS_THE_MAIN_FUNESTRA_ANSWERS = frozenset({
    "PLAY_COMPILATION", "PLAY_FULL_VID", "PLAY_CLIP_JUMP", JUMP_TO_FUNSCRIPT, NEXT_FUNSCRIPTED,
})


MAIN_LOCK_COMMANDS = {
    "main_lock": TOGGLE_LOCK,
    "main_lock_on": LOCK_ON,
    "main_lock_off": LOCK_OFF,
}


_PLAYHEAD_COMMANDS: dict[str, str] = {
    f"{FunTimeFunestra.MAIN.label}_{act}": verb for act, verb in PLAYHEAD_ACTS.items()
}


def step_refused(funestra: FunTimeFunestra, verb: str, config: BridgeConfig) -> list[WindowOp]:
    status = read_scripted_status(funestra, config.funestras[funestra])
    cannot = why_it_cannot_step(verb, status, VIDEO)
    if cannot is None:
        return []
    return [WindowOp(op="message", key=cannot.capitalize(),
                     source=FUNESTRA_SOURCES[funestra], level=logging.WARNING)]


def refused_on_a_satellite(funestra: FunTimeFunestra, act: str, state: BridgeState,
                           config: BridgeConfig) -> list[WindowOp]:
    if FUNSCRIPT_MOVES.get(act) == JUMP_TO_FUNSCRIPT:
        return skip_ahead_refused(funestra, state)
    verb = PLAYHEAD_ACTS.get(act)
    return step_refused(funestra, verb, config) if verb else []


def _step_through(verb: str, state: BridgeState, config: BridgeConfig,
                  _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if refused := step_refused(FunTimeFunestra.MAIN, verb, config):
        return state, refused
    append_command(config.main_funestra.cmd_file, verb)
    return state, []


def _cmd_file_of_whatever_is_on_the_main_screen(state: BridgeState, config: BridgeConfig) -> Path:
    return config.main_funestra.cmd_file if kino_shows(state.main_mode) else config.genau.cmd_file


def _locked_as_whatever_is_on_the_main_screen_reports(state: BridgeState, config: BridgeConfig) -> bool:
    if kino_shows(state.main_mode):
        return read_main_funestra_status(config.main_funestra.status_file).locked
    return read_genau_status(config.genau.status_file).locked


def _forward_to_whatever_is_on_the_main_funestra(
    verb: str, state: BridgeState, config: BridgeConfig, _target_path: str,
) -> tuple[BridgeState, list[WindowOp]]:
    append_command(_cmd_file_of_whatever_is_on_the_main_screen(state, config), verb)
    return state, []


def _forward_to_main_funestra_on_screen(verb: str, state: BridgeState, config: BridgeConfig,
                              _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if not kino_shows(state.main_mode):
        return ignored(state, IN_GENAU_MODE)
    append_command(config.main_funestra.cmd_file, verb)
    if verb in _VERBS_THE_MAIN_FUNESTRA_ANSWERS and not config.vr_main_funestra:
        return state, [WindowOp(op="main_funestra_answers")]
    return state, []


def _skip_ahead_on_the_main_funestra(state: BridgeState, config: BridgeConfig,
                                     target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if kino_shows(state.main_mode) and (refused := skip_ahead_refused(FunTimeFunestra.MAIN, state)):
        return state, refused
    return _forward_to_main_funestra_on_screen(
        MAIN_FUNESTRA_CMD_MAP["main_funestra_funscript_jump"], state, config, target_path)


def _forward_to_the_vr_main_funestra(verb: str, state: BridgeState, config: BridgeConfig,
                                   target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if not config.vr_main_funestra:
        return ignored(state, OUTSIDE_VR)
    aimed = verb in AIMED_AT_A_VIDEO and target_path
    append_command(config.main_funestra.cmd_file, f"{verb} {target_path}" if aimed else verb)
    return state, []


def _main_lock(verb: str, state: BridgeState, config: BridgeConfig,
               target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if kino_shows(state.main_mode) and _gone_from_kinos_screen(target_path, config):
        if verb == LOCK_OFF:
            return state, []
        append_command(config.main_funestra.cmd_file,
                       play_file(scripted_item(target_path, config.regen_metadata_root)))
        verb = LOCK_ON
    locked = _locked_as_whatever_is_on_the_main_screen_reports(state, config)
    append_command(_cmd_file_of_whatever_is_on_the_main_screen(state, config), verb)
    if verb == LOCK_ON or (verb == TOGGLE_LOCK and not locked):
        return state, [WindowOp(op=Op.FOLLOW_THE_LOCK, key=state.main_mode)]
    return state, []


def _gone_from_kinos_screen(target_path: str, config: BridgeConfig) -> bool:
    on_screen = read_main_funestra_status(config.main_funestra.status_file).video
    return bool(target_path) and bool(on_screen) and not same_file_to_windows(target_path, on_screen)


def _save_video(state: BridgeState, _config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if state.main_mode == MAIN_GENAU_MODE:
        return ignored(state, IN_GENAU_MODE)
    return state, [WindowOp(op="save_clip")]


def _trash_kinos_video(state: BridgeState, config: BridgeConfig,
                       target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if not kino_shows(state.main_mode):
        return ignored(state, IN_GENAU_MODE)
    status = read_main_funestra_status(config.main_funestra.status_file)
    video = target_path or status.video
    if not video:
        return state, []
    on_screen = same_file_to_windows(video, status.video)
    if on_screen and status.locked:
        append_command(config.main_funestra.cmd_file, LOCK_OFF)
    if unfavorited(video, config):
        if on_screen:
            append_command(config.main_funestra.cmd_file, NEXT)
        return state, [WindowOp(op="message", key=UNFAVORITED, source=SOURCE_MAIN)]
    append_command(config.main_funestra.cmd_file, f"{TRASH} {video}")
    written_for_the_hosted_app([trash_file_line(video)], config)
    return state, [WindowOp(op="message", key=SENT_TO_TRASH, source=SOURCE_MAIN)]


_MAIN_TRANSPORT = {"main_prev": PREV, "main_next": NEXT}

SKIP_AHEAD = "main_funestra_funscript_jump"

HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_step_through, verb) for cmd, verb in _PLAYHEAD_COMMANDS.items()},
    **{cmd: partial(_forward_to_whatever_is_on_the_main_funestra, verb)
       for cmd, verb in _MAIN_TRANSPORT.items()},
    **{cmd: partial(_main_lock, verb) for cmd, verb in MAIN_LOCK_COMMANDS.items()},
    **{cmd: partial(_forward_to_the_vr_main_funestra, verb)
       for cmd, verb in VR_MAIN_FUNESTRA_VERBS.items()},
    **{cmd: partial(_forward_to_main_funestra_on_screen, verb)
       for cmd, verb in MAIN_FUNESTRA_CMD_MAP.items() if cmd != SKIP_AHEAD},
    SKIP_AHEAD: _skip_ahead_on_the_main_funestra,
    "genaumacher_save": _save_video,
    "main_trash": _trash_kinos_video,
}
