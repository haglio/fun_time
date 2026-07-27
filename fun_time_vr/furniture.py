"""When the in-video furniture actually needs repainting — pure decisions.

The VR player repaints each unit's scrubber and volume chip from its pump
tick, but the bitmaps only change when something they show moves: the
playcursor advances one track pixel every few hundred milliseconds on a
typical clip, and the volume chip a few times a session.  Repainting on every
tick re-allocates and re-uploads a full-width bitmap per unit per tick — real
CPU on the desktop, and in VR it was most of the pump's cost — so each unit
keeps the last painted state and repaints only when these keys move.
"""
from __future__ import annotations

from player_core.timeline import bar_track_x, bar_x
from player_core.volume import VolumeHud


def scrubber_state(
    width: int, height: int, position_ms: float, duration_ms: float
) -> tuple[int, int, int]:
    """Everything the scrubber bitmap and its placement depend on.

    The playcursor lands on a track pixel; until it crosses to the next one the
    painted bar is byte-identical, however much ``position_ms`` moved.
    """
    x0, x1 = bar_track_x(width)
    return (width, height, bar_x(position_ms, duration_ms, x0, x1))


def chip_state(width: int, height: int, hud: VolumeHud) -> tuple[int, int, VolumeHud]:
    """Everything the volume chip and its placement depend on."""
    return (width, height, hud)
