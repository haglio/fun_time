from __future__ import annotations

import logging
from dataclasses import replace

from .bridge_records import BridgeConfig, WindowOp
from .origenerator_switches import RfbMode
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


def origenerator_refused(state: BridgeState, config: BridgeConfig, *,
                         asked_for: bool) -> list[WindowOp] | None:
    if not config.origenerator_enabled:
        return [WindowOp(op="message", key="No Origenerator configured",
                         level=logging.WARNING)]
    if asked_for and not state.origenerator_ready:
        return [WindowOp(op="message", key="Origenerator is still starting",
                         level=logging.WARNING)]
    return None


def switch_the_rfb_mode(target: RfbMode, state: BridgeState, config: BridgeConfig,
                        _target_path: str = "") -> tuple[BridgeState, list[WindowOp]]:
    refused = origenerator_refused(state, config, asked_for=target is RfbMode.ORIGENERATOR)
    if refused is not None:
        return state, refused
    if state.rfb_mode is target:
        return state, []
    logger.info("Origenerator mode" if target is RfbMode.ORIGENERATOR else "Random Favs Browser mode")
    return replace(state, rfb_mode=target), _rfb_mode_ops(target)
