from __future__ import annotations

import logging
from functools import partial

from funestra.file_channel import append_command
from funestra.funestra_verbs import NEXT, PREV

from .bridge_records import BridgeConfig, Handler, Route, WindowOp
from .command_value import Command
from .event_log import SOURCE_MAIN
from .funestra_status import read_genau_status
from .lock import SENT_TO_TRASH, unfavorite
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


_THE_MOTION_READOUTS_OWN_PLUS_AND_MINUS = {
    "robot_hand_speed_down": "SPEED_DOWN",
    "robot_hand_speed_up": "SPEED_UP",
}


_SENT_TO_THE_HAND_IN_EITHER_MODE = {
    "robot_hand_amplitude_down": "AMPLITUDE_DOWN",
    "robot_hand_amplitude_up": "AMPLITUDE_UP",
    "robot_hand_center_down": "CENTER_DOWN",
    "robot_hand_center_up": "CENTER_UP",
    "robot_hand_cycle_shape": "CYCLE_SHAPE",
    "robot_hand_cycle_shape_prev": "CYCLE_SHAPE_PREV",
    "robot_hand_toggle_learned": "TOGGLE_LEARNED",
    "robot_hand_learned_on": "LEARNED_ON",
    "robot_hand_learned_off": "LEARNED_OFF",
    "genau_flick_seconds_down": "FLICK_SECONDS_DOWN",
    "genau_flick_seconds_up": "FLICK_SECONDS_UP",
    "genau_prev_flick": PREV,
    "genau_next_flick": NEXT,
    "genau_flip_ends": "FLIP_ENDS",
    **_THE_MOTION_READOUTS_OWN_PLUS_AND_MINUS,
}


_GENAUS_FLICK_SECONDS = "genau_flick_seconds"


_GENAUS_FLICK_COMMANDS = frozenset({
    "genau_prev_flick", "genau_next_flick", "genau_trash_flick", "genau_flip_ends",
    _GENAUS_FLICK_SECONDS, f"{_GENAUS_FLICK_SECONDS}_down", f"{_GENAUS_FLICK_SECONDS}_up",
})


def about_genaus_flick(command: Command) -> bool:
    return command.action in _GENAUS_FLICK_COMMANDS


_NUMERIC_COMMANDS = {
    "robot_hand_amp": "AMP",
    "robot_hand_center": "CENTER",
    "robot_hand_speed": "SPEED",
    _GENAUS_FLICK_SECONDS: "FLICK_SECONDS",
}


def _forward_to_genau(verb: str, state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    append_command(config.genau_cmd_file, verb)
    return state, []


def _send_genaus_flick_to_trash(state: BridgeState, config: BridgeConfig,
                            _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    unfavorite(read_genau_status(config.genau_status_file).flick, config)
    append_command(config.genau_cmd_file, "TRASH")
    return state, [WindowOp(op="message", key=SENT_TO_TRASH, source=SOURCE_MAIN)]


def _numeric(keyword: str, command: Command, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _forward_to_genau(f"{keyword} {command.argument}", state, config, _target_path)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_forward_to_genau, verb) for cmd, verb in _SENT_TO_THE_HAND_IN_EITHER_MODE.items()},
    "genau_trash_flick": _send_genaus_flick_to_trash,
}

VALUED: dict[str, Route] = {command: partial(_numeric, keyword)
                             for command, keyword in _NUMERIC_COMMANDS.items()}
