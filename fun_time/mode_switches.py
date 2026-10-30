from __future__ import annotations

import logging
from dataclasses import replace
from functools import partial

from funestra.modes import SatellitesMode

from .bridge_records import BridgeConfig, Handler, WindowOp
from .dashboard_actions import ORIGENERATOR_ACTIVATE, RFB_ACTIVATE
from .mode_plan import MAIN_GENAU_MODE, MAIN_KINO_MODE
from .origenerator_switches import (
    BOTH_SATELLITES_MODE_COMMANDS,
    KINO_MODE,
    ORIGENERATOR_MODE,
    SATELLITE_MODE_COMMANDS,
    RfbSlot,
    hosts,
    origenerator_shows,
)
from .rfb_slot import origenerator_refused, switch_the_rfb_slot
from .room_funestras import RoomFunestra
from .runtime_flow import apply_mode_switch, apply_satellite_switch
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


def _main_slot_ops() -> list[WindowOp]:
    """The main slot on a mode switch: Kino and Genau share the Main Funestra's
    window, which the switch has already told which of them to show, so what is
    left is to bring that window back if it was parked and hand it the focus,
    so focus never falls through to another application."""
    return [
        WindowOp(op="show_role", key="main_funestra"),
        WindowOp(op="activate_role", key="main_funestra"),
    ]


# The main slot's two mode switches, by their target mode.
_MODE_SWITCH_COMMANDS: dict[str, str] = {
    "genau_activate": MAIN_GENAU_MODE,
    "main_kino_activate": MAIN_KINO_MODE,
}


def _switch_satellite(funestra: RoomFunestra, target: SatellitesMode, state: BridgeState,
                      config: BridgeConfig) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_satellite_switch(
        funestra=funestra,
        current_mode=state.satellite(funestra).mode,
        target_mode=target,
        origenerator_cmd_file=config.origenerator_cmd_file,
        channel=config.satellite(funestra),
    )
    logger.info(result.log_message)
    state = state.with_satellite(funestra, mode=result.next_mode)
    if result.is_transition and not origenerator_shows(result.next_mode):
        return state, [WindowOp(op="take_back_funestras", key=funestra.label)]
    return state, []


def _dispatch_mode_switch(
    target_mode: str, state: BridgeState, config: BridgeConfig, ops: list[WindowOp]
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_mode_switch(
        current_mode=state.main_mode,
        target_mode=target_mode,
        omni_paused=state.omni_paused,
        main_funestra_paused_file=config.main_funestra_paused_file,
        main_funestra_cmd_file=config.main_funestra_cmd_file,
    )
    state = replace(state, main_mode=result.next_mode)
    if result.is_transition:
        ops.extend(_main_slot_ops())
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _kino_activate(state: BridgeState, config: BridgeConfig,
                   _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    """"kino mode", said of no side: the main slot's video AND the satellites'
    Funestras, each through its own switch — a session hosting no Origenerator
    has only the main slot's to make."""
    state, ops = _dispatch_mode_switch(MAIN_KINO_MODE, state, config, [])
    if config.origenerator_enabled:
        state, more = _both_satellites_switch(KINO_MODE, state, config, _target_path)
        ops.extend(more)
    return state, ops


def _mode_switch(target: str, state: BridgeState, config: BridgeConfig,
                 _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch_mode_switch(target, state, config, [])


def _satellite_switch(funestra: RoomFunestra, target: SatellitesMode, state: BridgeState,
                      config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    refused = origenerator_refused(state, config, asked_for=origenerator_shows(target))
    if refused is not None:
        return state, refused
    return _switch_satellite(funestra, target, state, config)


def _both_satellites_switch(target: SatellitesMode | None, state: BridgeState,
                            config: BridgeConfig,
                            _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    if target is None:
        held = all(hosts(funestra, state, config) for funestra in RoomFunestra.SATELLITES)
        target = KINO_MODE if held else ORIGENERATOR_MODE
    refused = origenerator_refused(state, config, asked_for=origenerator_shows(target))
    if refused is not None:
        return state, refused
    ops: list[WindowOp] = []
    for funestra in RoomFunestra.SATELLITES:
        state, more = _switch_satellite(funestra, target, state, config)
        ops.extend(more)
    return state, ops


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_mode_switch, target) for cmd, target in _MODE_SWITCH_COMMANDS.items()},
    **{cmd: partial(_satellite_switch, funestra, mode)
       for cmd, (funestra, mode) in SATELLITE_MODE_COMMANDS.items()},
    **{cmd: partial(_both_satellites_switch, mode)
       for cmd, mode in BOTH_SATELLITES_MODE_COMMANDS.items()},
    RFB_ACTIVATE: partial(switch_the_rfb_slot, RfbSlot.RFB),
    ORIGENERATOR_ACTIVATE: partial(switch_the_rfb_slot, RfbSlot.ORIGENERATOR),
    "kino_activate": _kino_activate,
}
