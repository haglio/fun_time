from __future__ import annotations

import logging
from dataclasses import replace
from functools import partial

from app_support import osr2_hold
from funestra.file_channel import append_command
from funestra.funestra_verbs import (
    JUMP_TO_FUNSCRIPT,
    NEXT_FUNSCRIPTED,
    SET_SPEED,
    SPEED_DOWN,
    SPEED_UP,
    set_speed,
)
from funestra.main_hud import HOLD_VERB, OSR2_CONTROL_BUTTONS, OSR2_DRIVING, OSR2_PARKED

from .bridge_records import IN_GENAU_MODE, BridgeConfig, Handler, Route, WindowOp, ignored
from .broker_control import write_broker_command
from .command_value import Command
from .fun_time_funestras import FunTimeFunestra
from .funestra_status import read_main_funestra_status
from .mode_plan import kino_shows
from .osr2_section import TAKE_OSR2_COMMANDS, funestra_with_the_osr2
from .satellite_control import read_satellite_status
from .satellite_groups import send_satellite
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


_SPEED_BY_DRIVER = {
    "speed_down": SPEED_DOWN,
    "speed_up": SPEED_UP,
}


FUNSCRIPT_MOVES = {
    "funscript_jump": JUMP_TO_FUNSCRIPT,
    "next_funscripted": NEXT_FUNSCRIPTED,
}


PLAYBACK_RATE_ACTS = {
    "speed_down": SPEED_DOWN,
    "speed_up": SPEED_UP,
    "speed_min": f"{SET_SPEED} min",
    "speed_max": f"{SET_SPEED} max",
}


_GENAU_RATE_ENDS = {
    "speed_min": "SPEED 0",
    "speed_max": "SPEED 100",
}


def percent_rate(value: str) -> str | None:
    try:
        pct = int(value)
    except ValueError:
        return None
    return set_speed(pct / 100)


def _the_video_whose_clock_paces_the_osr2(holder: FunTimeFunestra,
                                          config: BridgeConfig) -> FunTimeFunestra | None:
    if holder is FunTimeFunestra.MAIN:
        scripted = read_main_funestra_status(config.main_funestra_status_file).funscript_driving
    else:
        scripted = read_satellite_status(config.satellite(holder).status_file).funscript_driving
    return holder if scripted else None


def _speed_target(state: BridgeState, config: BridgeConfig, *,
                  by_driver: bool) -> FunTimeFunestra | None:
    holder = funestra_with_the_osr2(state)
    the_motion_can_take_it = by_driver and state.osr2_control == OSR2_DRIVING
    if the_motion_can_take_it and holder is not FunTimeFunestra.MAIN:
        return _the_video_whose_clock_paces_the_osr2(holder, config)
    if not kino_shows(state.main_mode):
        return None
    if not the_motion_can_take_it:
        return FunTimeFunestra.MAIN
    return _the_video_whose_clock_paces_the_osr2(holder, config)


_SKIP_AHEAD_NAMES = {
    FunTimeFunestra.MAIN: "Main Funestra",
    FunTimeFunestra.PORTRAIT: "portrait Satellite",
    FunTimeFunestra.LANDSCAPE: "landscape Satellite",
}


def skip_ahead_refused(funestra: FunTimeFunestra, state: BridgeState) -> list[WindowOp]:
    if funestra_with_the_osr2(state) is funestra:
        return []
    return [WindowOp(op="message", key=f"The {_SKIP_AHEAD_NAMES[funestra]} doesn't have the OSR2",
                     level=logging.WARNING)]


def _speed(main_funestra_cmd: str | None, genau_cmd: str | None, by_driver: bool,
           state: BridgeState, config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    target = _speed_target(state, config, by_driver=by_driver)
    verb = genau_cmd if target is None else main_funestra_cmd
    if verb is None:
        return ignored(state, IN_GENAU_MODE)
    if target is None:
        append_command(config.genau_cmd_file, verb)
    elif target is FunTimeFunestra.MAIN:
        append_command(config.main_funestra_cmd_file, verb)
    else:
        send_satellite(config, target, verb)
    return state, []


def robot_hand_hold(control: str, state: BridgeState, config: BridgeConfig,
                _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if config.broker_cmd_file is not None:
        write_broker_command(config.broker_cmd_file, HOLD_VERB[control])
    return replace(state, osr2_control=control), []


def _robot_hand_release(state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if config.broker_cmd_file is not None:
        write_broker_command(config.broker_cmd_file, osr2_hold.RESUME)
    return replace(state, osr2_control=OSR2_DRIVING), []


def _take_the_osr2(funestra: FunTimeFunestra, state: BridgeState, _config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return replace(state, osr2_funestra=funestra), []


def _main_funestra_speed(command: Command, state: BridgeState, config: BridgeConfig,
                         _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    main_funestra_cmd = percent_rate(command.argument)
    if main_funestra_cmd is None:
        return state, []
    return _speed(main_funestra_cmd, None, False, state, config, _target_path)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_speed, verb, verb, True) for cmd, verb in _SPEED_BY_DRIVER.items()},
    **{f"main_funestra_{act}": partial(_speed, verb, _GENAU_RATE_ENDS.get(act), False)
       for act, verb in PLAYBACK_RATE_ACTS.items()},
    OSR2_CONTROL_BUTTONS[OSR2_PARKED]: partial(robot_hand_hold, OSR2_PARKED),
    OSR2_CONTROL_BUTTONS[OSR2_DRIVING]: _robot_hand_release,
    **{cmd: partial(_take_the_osr2, funestra) for cmd, funestra in TAKE_OSR2_COMMANDS.items()},
}

VALUED: dict[str, Route] = {"main_funestra_speed": _main_funestra_speed}
