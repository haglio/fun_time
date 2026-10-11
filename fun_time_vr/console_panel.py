"""The main console, hanging in the headset: one size, and which edge it hangs along."""
from __future__ import annotations

import logging

from funestra.hud_placement import HudEdge
from shared_ui.palette import AMBER, GREEN, RED, TEXT_MUTED, TEXT_PRIMARY

from fun_time.event_log import FAVORITE, NOTICE

DEG_PER_PX = 32.6 / 380  # every control in the scene, one size
# Pixels across, held: the screen keeps one size whatever it carries, so its
# bitmap never rescales.
PANEL_WIDTH_PX = 560

# fun_time.log_panel's own mapping.
_LEVEL_COLORS: dict[int, tuple[int, int, int]] = {
    NOTICE: TEXT_PRIMARY,
    FAVORITE: GREEN,
    logging.WARNING: AMBER,
    logging.ERROR: RED,
}


def level_color(level: int) -> tuple[int, int, int]:
    """The color for *level*, rounding down to the loudest level it reaches."""
    for threshold in sorted(_LEVEL_COLORS, reverse=True):
        if level >= threshold:
            return _LEVEL_COLORS[threshold]
    return TEXT_MUTED


def panel_hangs_from(edge: HudEdge, *, wrapped: bool) -> HudEdge:
    return HudEdge.LOWER if wrapped else edge
