from __future__ import annotations

from funestra.hud_placement import HudEdge

DEG_PER_PX = 32.6 / 380
HUD_WIDTH_PX = 560

_UNDER_THE_DASHBOARD = HudEdge.LOWER


def hud_hangs_from(edge: HudEdge, *, wrapped: bool) -> HudEdge:
    the_video_has_no_edge_to_hang_against = wrapped
    return _UNDER_THE_DASHBOARD if the_video_has_no_edge_to_hang_against else edge
