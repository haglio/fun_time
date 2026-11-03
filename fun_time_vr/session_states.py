from __future__ import annotations

import logging
from collections.abc import Callable

import xr

logger = logging.getLogger(__name__)

_DRAWS_FRAMES = (xr.SessionState.VISIBLE, xr.SessionState.FOCUSED)
_CAN_BE_ASKED_TO_END = (xr.SessionState.READY, xr.SessionState.SYNCHRONIZED, *_DRAWS_FRAMES)
_OVER = (xr.SessionState.LOSS_PENDING, xr.SessionState.EXITING)


class SessionStates:
    def __init__(self, *, begin: Callable[[], None], end: Callable[[], None]) -> None:
        self._begin = begin
        self._end = end
        self._state = xr.SessionState.UNKNOWN
        self.running = True
        self.begun = False

    def heard(self, state: xr.SessionState) -> None:
        self._state = state
        logger.info("Session state -> %s", state.name)
        if state == xr.SessionState.READY:
            self._begin()
            self.begun = True
        elif state == xr.SessionState.STOPPING:
            self._end()
            self.begun = False
        elif state in _OVER:
            self.running = False

    @property
    def focused(self) -> bool:
        return self._state == xr.SessionState.FOCUSED

    @property
    def draws_frames(self) -> bool:
        return self._state in _DRAWS_FRAMES

    @property
    def can_be_asked_to_end(self) -> bool:
        return self._state in _CAN_BE_ASKED_TO_END
