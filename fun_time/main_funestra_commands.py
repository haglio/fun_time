from __future__ import annotations

import logging
from functools import partial

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

from .aiming import FUNESTRA_SOURCES
from .bridge_records import IN_GENAU_MODE, OUTSIDE_VR, BridgeConfig, Handler, Op, WindowOp, ignored
from .funestra_buttons import MAIN_FUNESTRA_NOUN, SATELLITE_NOUN
from .funestra_status import read_genau_status, read_main_funestra_status
from .mode_plan import MAIN_GENAU_MODE, kino_shows
from .osr2_commands import FUNSCRIPT_MOVES, skip_ahead_refused
from .room_funestras import RoomFunestra
from .satellite_control import read_satellite_status
from .satellite_groups import send_satellite
from .shared_state import BridgeState
from .ten_second_steps import why_it_cannot_step

logger = logging.getLogger(__name__)


_PLAYHEAD_ACTS = {
    "nudge_prev": SEEK_BACK,
    "nudge_next": SEEK_FWD,
}


MAIN_FUNESTRA_CMD_MAP = {
    "main_player_record_down": "RECORD_DOWN",
    "main_player_record_up": "RECORD_UP",
    "main_player_record_tap": "RECORD_TAP",
    "main_player_loop_cancel": "LOOP_CANCEL",
    "main_player_cycle_version": "CYCLE_VERSION",
    "main_player_cycle_version_back": "CYCLE_VERSION_BACK",
    "main_player_compilation": "PLAY_COMPILATION",
    "main_player_end_compilation": "END_COMPILATION",
    "main_player_full_vid": "PLAY_FULL_VID",
    "main_player_clip_jump": "PLAY_CLIP_JUMP",
    **{f"main_player_{act}": verb for act, verb in FUNSCRIPT_MOVES.items()},
}


_VERBS_THE_MAIN_FUNESTRA_ANSWERS = frozenset({
    "PLAY_COMPILATION", "PLAY_FULL_VID", "PLAY_CLIP_JUMP", JUMP_TO_FUNSCRIPT, NEXT_FUNSCRIPTED,
})


# The main slot's lock: repeat what is on screen, or let it move on — the Main Funestra's
# video into the next playlist entry, Genau's flick into the next flick after its
# interval.  Both Funestras answer these three verbs and both open locked (the
# routing is :func:`_main_lock`'s).  The toggle is the key and the button; the
# absolute pair is what the spoken forms send, since a speaker asks for the
# state they want.
MAIN_LOCK_COMMANDS = {
    "main_lock": TOGGLE_LOCK,
    "main_lock_on": LOCK_ON,
    "main_lock_off": LOCK_OFF,
}


_PLAYHEAD_COMMANDS: dict[str, tuple[RoomFunestra, str]] = {
    f"{funestra.label}_{act}": (funestra, verb)
    for funestra in RoomFunestra
    for act, verb in _PLAYHEAD_ACTS.items()
}


def _step_through(funestra: RoomFunestra, verb: str, state: BridgeState, config: BridgeConfig,
                  _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if funestra is RoomFunestra.MAIN:
        status = read_main_funestra_status(config.main_funestra_status_file)
        noun = MAIN_FUNESTRA_NOUN
    else:
        status = read_satellite_status(config.satellite(funestra).status_file)
        noun = SATELLITE_NOUN
    cannot = why_it_cannot_step(verb, status, noun)
    if cannot is not None:
        return state, [WindowOp(op="notice", key=cannot.capitalize(),
                                source=FUNESTRA_SOURCES[funestra], level=logging.WARNING)]
    if funestra is RoomFunestra.MAIN:
        append_command(config.main_funestra_cmd_file, verb)
    else:
        send_satellite(config, funestra, verb)
    return state, []


def _forward_to_main_funestra(verb: str, state: BridgeState, config: BridgeConfig,
                    _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """The Main Funestra owns the main slot in kino mode; in genau mode the paused Main Funestra
    still navigates in the background, and its SEEK commands apply to a live
    local clock, so rapid nudges stack naturally."""
    append_command(config.main_funestra_cmd_file, verb)
    return state, []


def _forward_to_main_funestra_on_screen(verb: str, state: BridgeState, config: BridgeConfig,
                              _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """Loop recording, versions and length only make sense while the Main Funestra owns the
    main slot — kino mode, not genau."""
    if not kino_shows(state.main_mode):
        return ignored(state, IN_GENAU_MODE)
    append_command(config.main_funestra_cmd_file, verb)
    if verb in _VERBS_THE_MAIN_FUNESTRA_ANSWERS and not config.vr_main_funestra:
        return state, [WindowOp(op="main_funestra_answers")]
    return state, []


def _skip_ahead_on_the_main_funestra(state: BridgeState, config: BridgeConfig,
                                     target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if kino_shows(state.main_mode) and (refused := skip_ahead_refused(RoomFunestra.MAIN, state)):
        return state, refused
    return _forward_to_main_funestra_on_screen(
        MAIN_FUNESTRA_CMD_MAP["main_player_funscript_jump"], state, config, target_path)


def _forward_to_the_vr_main_funestra(verb: str, state: BridgeState, config: BridgeConfig,
                                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if not config.vr_main_funestra:
        return ignored(state, OUTSIDE_VR)
    append_command(config.main_funestra_cmd_file, verb)
    return state, []


def _main_lock(verb: str, state: BridgeState, config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if kino_shows(state.main_mode):
        cmd_file = config.main_funestra_cmd_file
        locked = read_main_funestra_status(config.main_funestra_status_file).locked
    else:
        cmd_file = config.genau_cmd_file
        locked = read_genau_status(config.genau_status_file).locked
    append_command(cmd_file, verb)
    if verb == LOCK_ON or (verb == TOGGLE_LOCK and not locked):
        return state, [WindowOp(op=Op.FOLLOW_THE_LOCK, key=state.main_mode)]
    return state, []


def _lock_kino_on_the_main_funestra(verb: str, state: BridgeState, config: BridgeConfig,
                                    target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if not kino_shows(state.main_mode):
        return ignored(state, IN_GENAU_MODE)
    return _main_lock(verb, state, config, target_path)


def _save_clip(state: BridgeState, _config: BridgeConfig,
               _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """Ask the loop for a genaumacher save — asked for, not run: genaumacher boots a
    sibling repo's interpreter (up to its 10 s timeout) and this runs on the
    20 Hz tick, so the loop saves on a worker thread and flashes the result
    when it lands — the one notice that trails its keypress."""
    if state.main_mode == MAIN_GENAU_MODE:
        return ignored(state, IN_GENAU_MODE)
    return state, [WindowOp(op="save_clip")]


_MAIN_TRANSPORT = {"main_prev": PREV, "main_next": NEXT}

# Said of the headset's picture: its projection, where it hangs, and which scene.
_VR_MAIN_FUNESTRA_VERBS = {
    "projection_cycle": "CYCLE_PROJECTION",
    "projection_cycle_back": "CYCLE_PROJECTION_BACK",
    "projection_wider": "WIDEN_PROJECTION",
    "projection_narrower": "NARROW_PROJECTION",
    "recenter_view": "RECENTER",
    "tilt_up": "TILT_UP",
    "tilt_down": "TILT_DOWN",
    "tilt_reset": "TILT_RESET",
    "main_scene_prev": "PREV_SCENE",
    "main_scene_next": "NEXT_SCENE",
    "vr_reset": "LAYOUT_RESET",
}

SKIP_AHEAD = "main_player_funscript_jump"

HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_step_through, funestra, verb)
       for cmd, (funestra, verb) in _PLAYHEAD_COMMANDS.items()},
    **{cmd: partial(_forward_to_main_funestra, verb) for cmd, verb in _MAIN_TRANSPORT.items()},
    **{cmd: partial(_main_lock, verb) for cmd, verb in MAIN_LOCK_COMMANDS.items()},
    "genau_lock": partial(_main_lock, TOGGLE_LOCK),
    "main_player_lock": partial(_lock_kino_on_the_main_funestra, TOGGLE_LOCK),
    **{cmd: partial(_forward_to_the_vr_main_funestra, verb)
       for cmd, verb in _VR_MAIN_FUNESTRA_VERBS.items()},
    **{cmd: partial(_forward_to_main_funestra_on_screen, verb)
       for cmd, verb in MAIN_FUNESTRA_CMD_MAP.items() if cmd != SKIP_AHEAD},
    SKIP_AHEAD: _skip_ahead_on_the_main_funestra,
    "genaumacher_save": _save_clip,
}
