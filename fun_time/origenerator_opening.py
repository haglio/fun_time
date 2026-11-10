from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from .bridge_records import BridgeConfig
from .funestra_status import origenerator_has_published
from .origenerator_switches import RfbMode
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
        self._owed = config.origenerator_enabled
        self.puts_origenerator_in_the_browsers_place = (
            showing_a_slideshow or left_in.rfb_mode is RfbMode.ORIGENERATOR)

    @property
    def activates(self) -> bool:
        return self._owed

    def has_answered(self) -> bool:
        if self._is_up:
            return True
        if not self._config.origenerator_enabled or self._config.origenerator_status_file is None:
            return False
        self._is_up = origenerator_has_published(self._config.origenerator_status_file)
        if self._is_up:
            logger.info("Origenerator is up; its mode is open")
        return self._is_up

    def app_as_it_is(self, state: BridgeState) -> BridgeState:
        state = replace(state, origenerator_ready=self.has_answered())
        if self._config.origenerator_enabled and state.origenerator_ready:
            return state
        return with_the_browser_in_its_place(state)

    def opens_now(self, wait_for_the_app: Callable[[Callable[[], bool]], bool]) -> bool:
        owed, self._owed = self._owed, False
        if not owed:
            return False
        if wait_for_the_app(self.has_answered):
            return True
        raise OrigeneratorNeverCameUp
