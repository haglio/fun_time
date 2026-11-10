from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from .bridge_records import BridgeConfig
from .dashboard_actions import ORIGENERATOR_ACTIVATE
from .fun_time_funestras import FunTimeFunestra
from .funestra_status import origenerator_has_published
from .origenerator_switches import (
    KINO_MODE,
    ORIGENERATOR_MODE,
    RfbMode,
    mode_command,
)
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


class OrigeneratorNeverCameUp(Exception):
    pass


def holding_nothing(state: BridgeState) -> BridgeState:
    for funestra in FunTimeFunestra.SATELLITES:
        state = state.with_satellite(funestra, mode=KINO_MODE)
    return replace(state, rfb_mode=RfbMode.RFB)


def what_origenerator_held(left_in: BridgeState, *,
                           slideshow_on: tuple[FunTimeFunestra, ...]) -> tuple[str, ...]:
    satellites = tuple(
        mode_command(funestra.label, ORIGENERATOR_MODE) for funestra in FunTimeFunestra.SATELLITES)
    in_the_rfb_mode = bool(slideshow_on) or left_in.rfb_mode is RfbMode.ORIGENERATOR
    return (*satellites, *((ORIGENERATOR_ACTIVATE,) if in_the_rfb_mode else ()))


class OrigeneratorOpening:
    def __init__(self, config: BridgeConfig, left_in: BridgeState, *,
                 already_open: bool, slideshow_on: tuple[FunTimeFunestra, ...]) -> None:
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

    def app_as_it_is(self, state: BridgeState) -> BridgeState:
        state = replace(state, origenerator_ready=self.has_answered())
        if self._config.origenerator_enabled and state.origenerator_ready:
            return state
        return holding_nothing(state)

    def take_what_is_owed(
            self, wait_for_the_app: Callable[[Callable[[], bool]], bool]) -> tuple[str, ...]:
        owed, self._owed = self._owed, ()
        if not owed:
            return ()
        if wait_for_the_app(self.has_answered):
            return owed
        raise OrigeneratorNeverCameUp
