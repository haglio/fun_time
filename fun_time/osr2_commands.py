from __future__ import annotations

import logging
from dataclasses import replace
from functools import partial

from app_support import osr2_hold
from funestra.console import (
    HOLD_VERB,
    OSR2_CONTROL_BUTTONS,
    OSR2_DRIVING,
    OSR2_PARKED,
)
from funestra.file_channel import append_command
from funestra.funestra_verbs import (
    JUMP_TO_FUNSCRIPT,
    NEXT_FUNSCRIPTED,
    SET_SPEED,
    SPEED_DOWN,
    SPEED_UP,
)

from .bridge_records import IN_GENAU_MODE, BridgeConfig, Handler, WindowOp, ignored
from .broker_control import write_broker_command
from .funestra_status import read_main_funestra_status
from .mode_plan import kino_shows
from .osr2_section import TAKE_OSR2_COMMANDS, funestra_with_the_osr2
from .room_funestras import RoomFunestra
from .satellite_control import read_satellite_status
from .satellite_groups import send_satellite
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


# Speed control splits by which control said it: the console's ± marks move the
# engine they sit next to (_GENAU_CMD_MAP, PLAYBACK_RATE_ACTS), while this bare
# pair — spoken, or J/L — carries no label and follows whichever engine holds
# the OSR2 (see :func:`_speed_target` for the whole routing).
_SPEED_BY_DRIVER = {
    "speed_down": SPEED_DOWN,
    "speed_up": SPEED_UP,
}


# Past the lull the playhead is in, or on to the next scripted item: every
# Funestra answers both, the Main Funestra under the ids its console posts.
FUNSCRIPT_MOVES = {
    "funscript_jump": JUMP_TO_FUNSCRIPT,
    "next_funscripted": NEXT_FUNSCRIPTED,
}


# A video's own playback rate, as opposed to the motion's, said of one Funestra.
PLAYBACK_RATE_ACTS = {
    "speed_down": SPEED_DOWN,
    "speed_up": SPEED_UP,
    "speed_min": f"{SET_SPEED} min",
    "speed_max": f"{SET_SPEED} max",
}


# Where Genau is what shows, either end of the Main Funestra's range reaches its motion.
_GENAU_RATE_ENDS = {
    "speed_min": "SPEED 0",
    "speed_max": "SPEED 100",
}


def percent_rate(command: str, prefix: str) -> str | None:
    """'main_funestra_speed_150' -> 'SET_SPEED 1.5' (percent-of-normal -> multiplier)."""
    if not command.startswith(prefix):
        return None
    try:
        pct = int(command[len(prefix):])
    except ValueError:
        return None
    return f"{SET_SPEED} {pct / 100:g}"


def _speed_target(state: BridgeState, config: BridgeConfig, *,
                  by_driver: bool) -> RoomFunestra | None:
    """Which engine a speed command drives: a Funestra, or None for the Robot Hand.

    An engine-named command goes where its name says — in kino mode the video's
    rate to the Main Funestra, the one on screen; elsewhere the hand.  The
    unqualified nudge follows the OSR2: the funscript of the Funestra that has it
    while that script is driving, in any mode, else the Robot Hand, which is
    paused for the whole of a scripted stretch.  A device nobody is driving, held
    at either end or let go, has no motion to speed up, so the nudge reaches the
    video then.
    """
    holder = funestra_with_the_osr2(state)
    driving = by_driver and state.osr2_control == OSR2_DRIVING
    if driving and holder is not RoomFunestra.MAIN:
        status = read_satellite_status(config.satellite(holder).status_file)
        return holder if status.funscript_driving else None
    if not kino_shows(state.main_mode):
        return None
    if not driving:
        return RoomFunestra.MAIN
    if (holder is RoomFunestra.MAIN
            and read_main_funestra_status(config.main_funestra_status_file).funscript_driving):
        return RoomFunestra.MAIN
    return None


NORMAL_SPEED = f"{SET_SPEED} 1"


_SKIP_AHEAD_NAMES = {
    RoomFunestra.MAIN: "Main Funestra",
    RoomFunestra.PORTRAIT: "portrait Satellite",
    RoomFunestra.LANDSCAPE: "landscape Satellite",
}


def skip_ahead_refused(funestra: RoomFunestra, state: BridgeState) -> list[WindowOp]:
    if funestra_with_the_osr2(state) is funestra:
        return []
    return [WindowOp(op="notice", key=f"The {_SKIP_AHEAD_NAMES[funestra]} doesn't have the OSR2",
                     level=logging.WARNING)]


def _speed(main_funestra_cmd: str | None, genau_cmd: str | None, by_driver: bool,
           state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """Send a speed command to the engine it drives (see :func:`_speed_target`)."""
    target = _speed_target(state, config, by_driver=by_driver)
    verb = genau_cmd if target is None else main_funestra_cmd
    if verb is None:
        return ignored(state, IN_GENAU_MODE)
    if target is None:
        append_command(config.genau_cmd_file, verb)
    elif target is RoomFunestra.MAIN:
        append_command(config.main_funestra_cmd_file, verb)
    else:
        send_satellite(config, target, verb)
    return state, []


def robot_hand_hold(control: str, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """park / retract: the broker takes the device to that end and keeps it there,
    while the arbiter has both engines play on with nothing of them heard."""
    if config.broker_cmd_file is not None:
        write_broker_command(config.broker_cmd_file, HOLD_VERB[control])
    return replace(state, osr2_control=control), []


def _robot_hand_release(state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """unpark / OSR2 resume / the switch on: call the broker's hold off, and the
    arbiter hands the device back to whoever should have it."""
    if config.broker_cmd_file is not None:
        write_broker_command(config.broker_cmd_file, osr2_hold.RESUME)
    return replace(state, osr2_control=OSR2_DRIVING), []


def _take_the_osr2(funestra: RoomFunestra, state: BridgeState, _config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return replace(state, osr2_funestra=funestra), []


def _parsed_main_funestra_speed(command: str, state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    """"main_funestra_speed_<pct>" — an absolute video rate, the Main Funestra's alone."""
    main_funestra_cmd = percent_rate(command, "main_funestra_speed_")
    if main_funestra_cmd is None:
        return None
    return _speed(main_funestra_cmd, None, False, state, config, _target_path)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_speed, verb, verb, True) for cmd, verb in _SPEED_BY_DRIVER.items()},
    **{f"main_funestra_{act}": partial(_speed, verb, _GENAU_RATE_ENDS.get(act), False)
       for act, verb in PLAYBACK_RATE_ACTS.items()},
    OSR2_CONTROL_BUTTONS[OSR2_PARKED]: partial(robot_hand_hold, OSR2_PARKED),
    OSR2_CONTROL_BUTTONS[OSR2_DRIVING]: _robot_hand_release,
    **{cmd: partial(_take_the_osr2, funestra) for cmd, funestra in TAKE_OSR2_COMMANDS.items()},
}

PARSED_FORMS = (_parsed_main_funestra_speed,)
