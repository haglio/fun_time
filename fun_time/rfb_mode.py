from __future__ import annotations

import logging
from dataclasses import replace

from .bridge_records import BridgeConfig, WindowOp
from .origenerator_switches import WHAT_THE_DIM_BUTTONS_SAY, RfbMode
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


def _rfb_mode_ops(rfb_mode: RfbMode) -> list[WindowOp]:
    if rfb_mode is RfbMode.ORIGENERATOR:
        return [
            WindowOp(op="show_window", key="origenerator"),
            WindowOp(op="activate_window", key="origenerator"),
            WindowOp(op="restack_rfb"),
        ]
    return [WindowOp(op="hide_window", key="origenerator"), WindowOp(op="restack_rfb")]


def _origenerator_refused(state: BridgeState, *, asked_for: bool) -> list[WindowOp] | None:
    if asked_for and not state.origenerator_ready:
        return [WindowOp(op="message", key=WHAT_THE_DIM_BUTTONS_SAY, level=logging.WARNING)]
    return None


def switch_the_rfb_mode(target: RfbMode, state: BridgeState, _config: BridgeConfig,
                        _target_path: str = "") -> tuple[BridgeState, list[WindowOp]]:
    refused = _origenerator_refused(state, asked_for=target is RfbMode.ORIGENERATOR)
    if refused is not None:
        return state, refused
    if state.rfb_mode is target:
        return state, []
    logger.info("Origenerator mode" if target is RfbMode.ORIGENERATOR else "Random Faves Browser mode")
    return replace(state, rfb_mode=target), _rfb_mode_ops(target)
