from __future__ import annotations

import logging

from funestra.hud_placement import HudEdge
from shared_ui.palette import GREEN, RED, TEXT_MUTED, TEXT_PRIMARY, YELLOW

from fun_time.event_log import FAVORITE, MESSAGE

DEG_PER_PX = 32.6 / 380
HUD_WIDTH_PX = 560

_LEVEL_COLORS: dict[int, tuple[int, int, int]] = {
    MESSAGE: TEXT_PRIMARY,
    FAVORITE: GREEN,
    logging.WARNING: YELLOW,
    logging.ERROR: RED,
}


def level_color(level: int) -> tuple[int, int, int]:
    for threshold in sorted(_LEVEL_COLORS, reverse=True):
        if level >= threshold:
            return _LEVEL_COLORS[threshold]
    return TEXT_MUTED


def hud_hangs_from(edge: HudEdge, *, wrapped: bool) -> HudEdge:
    return HudEdge.LOWER if wrapped else edge
