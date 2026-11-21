from __future__ import annotations

import logging
from functools import partial

from funestra.file_channel import append_command
from funestra.funestra_verbs import NEXT, TRASH
from funestra.genau_buttons import ABOUT_GENAUS_FLICK, GENAU_VALUED, GENAU_VERBS, TRASH_FLICK

from .bridge_records import BridgeConfig, Handler, Route, WindowOp
from .command_value import Command
from .event_log import SOURCE_MAIN
from .funestra_status import read_genau_status
from .lock import SENT_TO_TRASH, UNFAVORITED, unfavorited
from .modes import same_file_to_windows
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
    "robot_hand_off": "ROBOT_HAND_OFF",
    **_THE_MOTION_READOUTS_OWN_PLUS_AND_MINUS,
    **GENAU_VERBS,
}


def about_genaus_flick(command: Command) -> bool:
    return command.action in ABOUT_GENAUS_FLICK


_NUMERIC_COMMANDS = {
    "robot_hand_amp": "AMP",
    "robot_hand_center": "CENTER",
    "robot_hand_speed": "SPEED",
    **GENAU_VALUED,
}


def _forward_to_genau(verb: str, state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    append_command(config.genau.cmd_file, verb)
    return state, []


def _send_genaus_flick_to_trash(state: BridgeState, config: BridgeConfig,
                            target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    on_screen = read_genau_status(config.genau.status_file).flick
    flick = target_path or on_screen
    if unfavorited(flick, config):
        if same_file_to_windows(flick, on_screen):
            append_command(config.genau.cmd_file, NEXT)
        return state, [WindowOp(op="message", key=UNFAVORITED, source=SOURCE_MAIN)]
    append_command(config.genau.cmd_file, f"{TRASH} {flick}" if flick else TRASH)
    return state, [WindowOp(op="message", key=SENT_TO_TRASH, source=SOURCE_MAIN)]


def _numeric(keyword: str, command: Command, state: BridgeState, config: BridgeConfig,
             _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _forward_to_genau(f"{keyword} {command.argument}", state, config, _target_path)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_forward_to_genau, verb) for cmd, verb in _SENT_TO_THE_HAND_IN_EITHER_MODE.items()},
    TRASH_FLICK: _send_genaus_flick_to_trash,
}

VALUED: dict[str, Route] = {command: partial(_numeric, keyword)
                             for command, keyword in _NUMERIC_COMMANDS.items()}
