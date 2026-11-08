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
)
from funestra.hud_controls import why_it_cannot_step

from .bridge_records import IN_GENAU_MODE, OUTSIDE_VR, BridgeConfig, Handler, Op, WindowOp, ignored
from .command_value import FUNESTRA_SOURCES
from .fun_time_funestras import FunTimeFunestra
from .funestra_buttons import NOUN
from .funestra_status import read_genau_status, read_main_funestra_status
from .mode_plan import MAIN_GENAU_MODE, kino_shows
from .osr2_commands import FUNSCRIPT_MOVES, skip_ahead_refused
from .satellite_control import read_satellite_status
from .satellite_groups import send_satellite
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


_PLAYHEAD_ACTS = {
    "nudge_prev": SEEK_BACK,
    "nudge_next": SEEK_FWD,
}


MAIN_FUNESTRA_CMD_MAP = {
    "main_funestra_record_down": "RECORD_DOWN",
    "main_funestra_record_up": "RECORD_UP",
    "main_funestra_record_tap": "RECORD_TAP",
    "main_funestra_loop_cancel": "LOOP_CANCEL",
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


_PLAYHEAD_COMMANDS: dict[str, tuple[FunTimeFunestra, str]] = {
    f"{funestra.label}_{act}": (funestra, verb)
    for funestra in FunTimeFunestra
    for act, verb in _PLAYHEAD_ACTS.items()
}


def _step_through(funestra: FunTimeFunestra, verb: str, state: BridgeState, config: BridgeConfig,
                  _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if funestra is FunTimeFunestra.MAIN:
        status = read_main_funestra_status(config.main_funestra_status_file)
    else:
        status = read_satellite_status(config.satellite(funestra).status_file)
    cannot = why_it_cannot_step(verb, status, NOUN)
    if cannot is not None:
        return state, [WindowOp(op="message", key=cannot.capitalize(),
                                source=FUNESTRA_SOURCES[funestra], level=logging.WARNING)]
    if funestra is FunTimeFunestra.MAIN:
        append_command(config.main_funestra_cmd_file, verb)
    else:
        send_satellite(config, funestra, verb)
    return state, []


def _cmd_file_of_whatever_is_on_the_main_screen(state: BridgeState, config: BridgeConfig) -> Path:
    return config.main_funestra_cmd_file if kino_shows(state.main_mode) else config.genau_cmd_file


def _locked_as_whatever_is_on_the_main_screen_reports(state: BridgeState, config: BridgeConfig) -> bool:
    if kino_shows(state.main_mode):
        return read_main_funestra_status(config.main_funestra_status_file).locked
    return read_genau_status(config.genau_status_file).locked


def _forward_to_whatever_is_on_the_main_funestra(
    verb: str, state: BridgeState, config: BridgeConfig, _target_path: str,
) -> tuple[BridgeState, list[WindowOp]]:
    append_command(_cmd_file_of_whatever_is_on_the_main_screen(state, config), verb)
    return state, []


def _forward_to_main_funestra_on_screen(verb: str, state: BridgeState, config: BridgeConfig,
                              _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if not kino_shows(state.main_mode):
        return ignored(state, IN_GENAU_MODE)
    append_command(config.main_funestra_cmd_file, verb)
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
                                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if not config.vr_main_funestra:
        return ignored(state, OUTSIDE_VR)
    append_command(config.main_funestra_cmd_file, verb)
    return state, []


def _main_lock(verb: str, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    locked = _locked_as_whatever_is_on_the_main_screen_reports(state, config)
    append_command(_cmd_file_of_whatever_is_on_the_main_screen(state, config), verb)
    if verb == LOCK_ON or (verb == TOGGLE_LOCK and not locked):
        return state, [WindowOp(op=Op.FOLLOW_THE_LOCK, key=state.main_mode)]
    return state, []


def _save_video(state: BridgeState, _config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if state.main_mode == MAIN_GENAU_MODE:
        return ignored(state, IN_GENAU_MODE)
    return state, [WindowOp(op="save_clip")]


_MAIN_TRANSPORT = {"main_prev": PREV, "main_next": NEXT}

_VR_MAIN_FUNESTRA_VERBS = {
    "projection_cycle": "CYCLE_PROJECTION",
    "projection_cycle_back": "CYCLE_PROJECTION_BACK",
    "recenter_view": "RECENTER",
    "tilt_up": "TILT_UP",
    "tilt_down": "TILT_DOWN",
    "tilt_reset": "TILT_RESET",
    "main_scene_prev": "PREV_SCENE",
    "main_scene_next": "NEXT_SCENE",
    "vr_reset": "LAYOUT_RESET",
}

SKIP_AHEAD = "main_funestra_funscript_jump"

HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_step_through, funestra, verb)
       for cmd, (funestra, verb) in _PLAYHEAD_COMMANDS.items()},
    **{cmd: partial(_forward_to_whatever_is_on_the_main_funestra, verb)
       for cmd, verb in _MAIN_TRANSPORT.items()},
    **{cmd: partial(_main_lock, verb) for cmd, verb in MAIN_LOCK_COMMANDS.items()},
    **{cmd: partial(_forward_to_the_vr_main_funestra, verb)
       for cmd, verb in _VR_MAIN_FUNESTRA_VERBS.items()},
    **{cmd: partial(_forward_to_main_funestra_on_screen, verb)
       for cmd, verb in MAIN_FUNESTRA_CMD_MAP.items() if cmd != SKIP_AHEAD},
    SKIP_AHEAD: _skip_ahead_on_the_main_funestra,
    "genaumacher_save": _save_video,
}
