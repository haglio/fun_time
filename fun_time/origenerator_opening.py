from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from .bridge_records import BridgeConfig
from .dashboard_actions import ORIGENERATOR_ACTIVATE
from .funestra_status import origenerator_has_published
from .origenerator_switches import (
    KINO_MODE,
    ORIGENERATOR_MODE,
    RfbSlot,
    mode_command,
    origenerator_shows,
)
from .room_funestras import RoomFunestra
from .shared_state import BridgeState, read_shared_state, write_shared_state

logger = logging.getLogger(__name__)


def holding_nothing(state: BridgeState) -> BridgeState:
    for funestra in RoomFunestra.SATELLITES:
        state = state.with_satellite(funestra, mode=KINO_MODE)
    return replace(state, rfb_slot=RfbSlot.RFB)


def what_origenerator_held(left_in: BridgeState, *,
                           slideshow_on: tuple[RoomFunestra, ...]) -> tuple[str, ...]:
    satellites = tuple(
        mode_command(funestra.label, ORIGENERATOR_MODE) for funestra in RoomFunestra.SATELLITES
        if funestra in slideshow_on or origenerator_shows(left_in.satellite(funestra).mode))
    in_the_rfb_slot = bool(slideshow_on) or left_in.rfb_slot is RfbSlot.ORIGENERATOR
    return (*satellites, *((ORIGENERATOR_ACTIVATE,) if in_the_rfb_slot else ()))


class OrigeneratorOpening:
    """The room opening in Origenerator mode: what the hosted app held when the
    last session closed is owed to it again, once it has answered."""

    def __init__(self, config: BridgeConfig, left_in: BridgeState, *,
                 already_open: bool, slideshow_on: tuple[RoomFunestra, ...]) -> None:
        self._config = config
        self._is_up = already_open
        self._owed = (what_origenerator_held(left_in, slideshow_on=slideshow_on)
                      if config.origenerator_enabled else ())

    @property
    def activates(self) -> bool:
        return bool(self._owed)

    def has_answered(self) -> bool:
        if self._is_up:
            return True
        if not self._config.origenerator_enabled or self._config.origenerator_status_file is None:
            return False
        self._is_up = origenerator_has_published(self._config.origenerator_status_file)
        if self._is_up:
            logger.info("Origenerator is up; its mode is open")
        return self._is_up

    def room_as_it_is(self, state: BridgeState) -> BridgeState:
        state = replace(state, origenerator_ready=self.has_answered())
        if self._config.origenerator_enabled and state.origenerator_ready:
            return state
        return holding_nothing(state)

    def take_what_is_owed(self, wait_for_the_app: Callable[[Callable[[], bool]], bool],
                          shared_state_file: Path) -> tuple[str, ...]:
        owed, self._owed = self._owed, ()
        if not owed:
            return ()
        if wait_for_the_app(self.has_answered):
            return owed
        logger.info("Origenerator did not finish starting, so the room opens in kino mode")
        left_in = read_shared_state(shared_state_file)
        if left_in is not None:
            write_shared_state(shared_state_file, holding_nothing(left_in))
        return ()
