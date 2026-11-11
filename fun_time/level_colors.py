from __future__ import annotations

import logging

from shared_ui.palette import GREEN, RED, TEXT_MUTED, TEXT_PRIMARY, YELLOW, Rgb

from fun_time.event_log import FAVORITE, MESSAGE

_LEVEL_COLORS: dict[int, Rgb] = {
    logging.DEBUG: TEXT_MUTED,
    logging.INFO: TEXT_MUTED,
    MESSAGE: TEXT_PRIMARY,
    FAVORITE: GREEN,
    logging.WARNING: YELLOW,
    logging.ERROR: RED,
}


def level_rgb(level: int) -> Rgb:
    for threshold in sorted(_LEVEL_COLORS, reverse=True):
        if level >= threshold:
            return _LEVEL_COLORS[threshold]
    return TEXT_PRIMARY
