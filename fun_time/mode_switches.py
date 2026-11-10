from __future__ import annotations

import logging
from dataclasses import replace
from functools import partial

from .bridge_records import BridgeConfig, Handler, WindowOp
from .dashboard_actions import ORIGENERATOR_ACTIVATE, RFB_ACTIVATE
from .mode_plan import MAIN_GENAU_MODE, MAIN_KINO_MODE
from .rfb_mode import switch_the_rfb_mode
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
        main_funestra_paused_file=config.main_funestra_paused_file,
        main_funestra_cmd_file=config.main_funestra_cmd_file,
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
    return switch_the_rfb_mode(RfbMode.ORIGENERATOR, replace(state, core_named_last=True), config,
                               target_path)


HANDLERS: dict[str, Handler] = {
    **{cmd: partial(_mode_switch, target) for cmd, target in _MODE_SWITCH_COMMANDS.items()},
    RFB_ACTIVATE: partial(switch_the_rfb_mode, RfbMode.RFB),
    ORIGENERATOR_ACTIVATE: _core_picked,
}
