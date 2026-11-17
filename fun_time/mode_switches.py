from __future__ import annotations

import logging
from dataclasses import replace
from functools import partial
from pathlib import Path

from funestra.file_channel import append_command
from funestra.funestra_verbs import play_file
from funestra.playlist import PlaylistItem

from .bridge_records import BridgeConfig, Handler, Route, WindowOp
from .command_value import Command
from .dashboard_actions import ORIGENERATOR_ACTIVATE, RFB_ACTIVATE
from .mode_plan import MAIN_GENAU_MODE, MAIN_KINO_MODE
from .modes import scripted_item
from .rfb_mode import core_shows, switch_the_rfb_mode
from .rfb_modes import RfbMode
from .runtime_flow import apply_mode_switch
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


def _main_funestra_ops() -> list[WindowOp]:
    return [
        WindowOp(op="show_window", key="main_funestra"),
        WindowOp(op="activate_window", key="main_funestra"),
    ]


_MODE_SWITCH_COMMANDS: dict[str, str] = {
    "genau_activate": MAIN_GENAU_MODE,
    "main_kino_activate": MAIN_KINO_MODE,
    "kino_activate": MAIN_KINO_MODE,
}


def _dispatch_mode_switch(
    target_mode: str, state: BridgeState, config: BridgeConfig, ops: list[WindowOp]
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_mode_switch(
        current_mode=state.main_mode,
        target_mode=target_mode,
        omni_paused=state.omni_paused,
        main_funestra_paused_file=config.main_funestra.paused_file,
        main_funestra_cmd_file=config.main_funestra.cmd_file,
    )
    state = replace(state, main_mode=result.next_mode)
    if result.is_transition:
        ops.extend(_main_funestra_ops())
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _mode_switch(target: str, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_mode_switch(target, state, config, [])


def _core_picked(state: BridgeState, config: BridgeConfig,
                 target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    state, ops = switch_the_rfb_mode(RfbMode.ORIGENERATOR, state, config, target_path)
    return (replace(state, core_named_last=True) if core_shows(state) else state), ops


PLAY_IN_KINO = "kino_play"


def _play_in_kino(command: Command, state: BridgeState, config: BridgeConfig,
                  _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    state, ops = _dispatch_mode_switch(MAIN_KINO_MODE, state, config, [])
    append_command(config.main_funestra.cmd_file,
                   play_file(scripted_item(command.argument, config.regen_metadata_root)))
    return state, ops


PLAY_IN_GENAU = "genau_play"


def _play_in_genau(command: Command, state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    state, ops = _dispatch_mode_switch(MAIN_GENAU_MODE, state, config, [])
    append_command(config.genau.cmd_file, play_file(PlaylistItem(Path(command.argument))))
    return state, ops


VALUED: dict[str, Route] = {PLAY_IN_KINO: _play_in_kino, PLAY_IN_GENAU: _play_in_genau}

HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_mode_switch, target) for cmd, target in _MODE_SWITCH_COMMANDS.items()},
    RFB_ACTIVATE: partial(switch_the_rfb_mode, RfbMode.RFB),
    ORIGENERATOR_ACTIVATE: _core_picked,
}
