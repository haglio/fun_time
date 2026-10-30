from __future__ import annotations

import logging
from functools import partial

from funestra.file_channel import append_command
from funestra.funestra_verbs import NEXT, PREV

from .bridge_records import BridgeConfig, Handler, WindowOp
from .event_log import SOURCE_MAIN
from .funestra_status import read_genau_status
from .lock import MARKED_WEIRD, unfavorite
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


_GENAU_CMD_MAP = {
    "robot_hand_amplitude_down": "AMPLITUDE_DOWN",
    "robot_hand_amplitude_up": "AMPLITUDE_UP",
    "robot_hand_center_down": "CENTER_DOWN",
    "robot_hand_center_up": "CENTER_UP",
    "robot_hand_cycle_shape": "CYCLE_SHAPE",
    "robot_hand_cycle_shape_prev": "CYCLE_SHAPE_PREV",
    "robot_hand_toggle_learned": "TOGGLE_LEARNED",
    "robot_hand_learned_on": "LEARNED_ON",
    "robot_hand_learned_off": "LEARNED_OFF",
    # How long an unlocked Genau leaves each flick on screen, a second at a
    # time; the padlock (MAIN_LOCK_COMMANDS) is the switch, this is its pace.
    "genau_flick_seconds_down": "FLICK_SECONDS_DOWN",
    "genau_flick_seconds_up": "FLICK_SECONDS_UP",
    "genau_prev_flick": PREV,
    "genau_next_flick": NEXT,
    "genau_flip_ends": "FLIP_ENDS",
    # The motion's rate as the console's own ± marks beside the wave send it —
    # Genau's alone; the unqualified pair is osr2_commands', following the OSR2.
    "robot_hand_speed_down": "SPEED_DOWN",
    "robot_hand_speed_up": "SPEED_UP",
}


_GENAUS_FLICK_SECONDS = "genau_flick_seconds_"


_GENAUS_FLICK_COMMANDS = frozenset({
    "genau_prev_flick", "genau_next_flick", "genau_weird_flick", "genau_lock", "genau_flip_ends",
})


def about_genaus_flick(command: str) -> bool:
    return command in _GENAUS_FLICK_COMMANDS or command.startswith(_GENAUS_FLICK_SECONDS)


_NUMERIC_PREFIXES = {
    "robot_hand_amp_": "AMP",
    "robot_hand_center_": "CENTER",
    "robot_hand_speed_": "SPEED",
    # Seconds a clip holds the screen, unlike the 0-100 axes above.
    _GENAUS_FLICK_SECONDS: "FLICK_SECONDS",
}


def _parse_numeric_command(command: str) -> str | None:
    """Parse 'robot_hand_amp_50' -> 'AMP 50', etc."""
    for prefix, keyword in _NUMERIC_PREFIXES.items():
        if command.startswith(prefix):
            value_str = command[len(prefix):]
            try:
                int(value_str)
            except ValueError:
                return None
            return f"{keyword} {value_str}"
    return None


def _forward_to_genau(verb: str, state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    append_command(config.genau_cmd_file, verb)
    return state, []


def _mark_genaus_flick_weird(state: BridgeState, config: BridgeConfig,
                            _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    unfavorite(read_genau_status(config.genau_status_file).flick, config)
    append_command(config.genau_cmd_file, "WEIRD")
    return state, [WindowOp(op="notice", key=MARKED_WEIRD, source=SOURCE_MAIN)]


def _parsed_numeric(command: str, state: BridgeState, config: BridgeConfig,
                          _target_path: str) -> tuple[BridgeState, list[WindowOp]] | None:
    genau_cmd = _parse_numeric_command(command)
    if genau_cmd is None:
        return None
    return _forward_to_genau(genau_cmd, state, config, _target_path)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_forward_to_genau, verb) for cmd, verb in _GENAU_CMD_MAP.items()},
    "genau_weird_flick": _mark_genaus_flick_weird,
}

PARSED_FORMS = (_parsed_numeric,)
