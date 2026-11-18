from __future__ import annotations

import logging

from shared_ui.message_shape import ERROR, FAVORITE, INFO, MESSAGE_INK, WARNING
from shared_ui.palette import TEXT_MUTED, Rgb

from fun_time.event_log import FAVORITE as FAVORITE_LEVEL
from fun_time.event_log import MESSAGE

_KIND_FROM_THE_LOUDEST_LEVEL_REACHED = (
    (logging.ERROR, ERROR), (logging.WARNING, WARNING), (FAVORITE_LEVEL, FAVORITE),
)


def message_kind(level: int) -> str:
    return next((kind for threshold, kind in _KIND_FROM_THE_LOUDEST_LEVEL_REACHED
                 if level >= threshold), INFO)


def level_rgb(level: int) -> Rgb:
    if level < MESSAGE:
        return TEXT_MUTED
    return MESSAGE_INK[message_kind(level)]
