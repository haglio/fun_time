from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from .bridge_records import BridgeConfig
from .funestra_status import origenerator_core_waits, origenerator_has_published
from .rfb_modes import RfbMode
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


class OrigeneratorNeverCameUp(Exception):
    pass


def with_the_browser_in_its_place(state: BridgeState) -> BridgeState:
    return replace(state, rfb_mode=RfbMode.RFB)


class OrigeneratorOpening:
    def __init__(self, config: BridgeConfig, left_in: BridgeState, *,
                 already_open: bool, showing_a_slideshow: bool) -> None:
        self._config = config
        self._is_up = already_open
        self._core_is_ready = False
        self._owed = True
        self._owes_core_the_browsers_place = (
            showing_a_slideshow or left_in.rfb_mode is RfbMode.ORIGENERATOR)

    @property
    def activates(self) -> bool:
        return self._owed

    def has_answered(self) -> bool:
        if self._is_up:
            return True
        self._is_up = origenerator_has_published(self._config.origenerator_status_file)
        if self._is_up:
            logger.info("Origenerator is up")
        return self._is_up

    def core_is_ready(self) -> bool:
        if not self._core_is_ready and self.has_answered():
            self._core_is_ready = not origenerator_core_waits(self._config.origenerator_status_file)
            if self._core_is_ready:
                logger.info("Origenerator Core is ready")
        return self._core_is_ready

    def app_as_it_is(self, state: BridgeState) -> BridgeState:
        state = replace(state, origenerator_ready=self.core_is_ready())
        if state.origenerator_ready:
            return state
        return with_the_browser_in_its_place(state)

    def gives_core_the_browsers_place_now(self, state: BridgeState) -> bool:
        if not (self._owes_core_the_browsers_place and state.origenerator_ready):
            return False
        self._owes_core_the_browsers_place = False
        return True

    def he_picked_what_shows_in_the_browsers_place(self) -> None:
        self._owes_core_the_browsers_place = False

    def opens_now(self, wait_for_the_app: Callable[[Callable[[], bool]], bool]) -> bool:
        owed, self._owed = self._owed, False
        if not owed:
            return False
        if wait_for_the_app(self.has_answered):
            return True
        raise OrigeneratorNeverCameUp
