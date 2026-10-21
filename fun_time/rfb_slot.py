"""What holds the Random Favs Browser's place -- the browser, or the hosted
Origenerator's own window over it -- and the place changing hands."""
from __future__ import annotations

import logging
from dataclasses import replace

from .bridge_records import BridgeConfig, WindowOp
from .origenerator_switches import RfbSlot
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


def _rfb_slot_ops(rfb_slot: RfbSlot) -> list[WindowOp]:
    if rfb_slot is RfbSlot.ORIGENERATOR:
        return [
            WindowOp(op="show_role", key="origenerator"),
            WindowOp(op="activate_role", key="origenerator"),
            WindowOp(op="restack_rfb"),
        ]
    return [WindowOp(op="hide_role", key="origenerator"), WindowOp(op="restack_rfb")]


def origenerator_refused(state: BridgeState, config: BridgeConfig, *,
                         asked_for: bool) -> list[WindowOp] | None:
    """The notice a switch toward Origenerator (*asked_for*) or away from it is
    refused with, or None.  Whatever draws its Origenerator button dim over the
    app's boot posts nothing; this answers the key and the spoken word, which
    have no dim button to look at."""
    if not config.origenerator_enabled:
        return [WindowOp(op="notice", key="No Origenerator configured",
                         level=logging.WARNING)]
    if asked_for and not state.origenerator_ready:
        return [WindowOp(op="notice", key="Origenerator is still starting",
                         level=logging.WARNING)]
    return None


def switch_the_rfb_slot(target: RfbSlot, state: BridgeState, config: BridgeConfig,
                        _target_path: str = "") -> tuple[BridgeState, list[WindowOp]]:
    refused = origenerator_refused(state, config, asked_for=target is RfbSlot.ORIGENERATOR)
    if refused is not None:
        return state, refused
    if state.rfb_slot is target:
        return state, []
    logger.info("Origenerator in the Random Favs Browser's place"
                if target is RfbSlot.ORIGENERATOR else "The Random Favs Browser back in its place")
    return replace(state, rfb_slot=target), _rfb_slot_ops(target)
