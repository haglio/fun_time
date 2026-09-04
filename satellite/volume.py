"""A satellite's own volume chip: what it shows, and what a press on it sets.

Set here rather than asked for, unlike Nau's: one sink, so nobody to ask.
"""
from __future__ import annotations

from dataclasses import replace

from player_core.volume import (
    MAX_VOLUME,
    MIN_VOLUME,
    VolumeHud,
    chip_local,
    hit_part,
    volume_at,
)


class SatelliteVolume:
    """The chip, and the mpv properties a press on it sets."""

    def __init__(self, player, *, live: bool = True) -> None:
        self._player = player
        self._live = live
        self._hud = VolumeHud(volume=MAX_VOLUME if live else MIN_VOLUME, muted=True)

    @property
    def hud(self) -> VolumeHud:
        """The level, and the mute drawn over it."""
        return self._hud

    def press_at(self, mx: int, my: int, *,
                 win_w: int, win_h: int, timeline_h: int) -> bool:
        """Take a press at window ``(mx, my)``; False if it missed the chip."""
        cx, cy = chip_local(mx, my, win_w=win_w, win_h=win_h, timeline_h=timeline_h)
        part = hit_part(cx, cy)
        if part and self._live:
            self._apply(part, cx)
        return bool(part)

    def drag_at(self, mx: int, my: int, *,
                win_w: int, win_h: int, timeline_h: int) -> None:
        """Set the level while the pointer is held on the track — only the track,
        so crossing the speaker on the way does not flip the mute."""
        cx, cy = chip_local(mx, my, win_w=win_w, win_h=win_h, timeline_h=timeline_h)
        if self._live and hit_part(cx, cy) == "track":
            self._apply("track", cx)

    def _apply(self, part: str, cx: int) -> None:
        if part == "mute":
            self._set(replace(self._hud, muted=not self._hud.muted))
        else:
            # Reaching for the slider lifts the mute, as the Windows mixer does.
            self._set(VolumeHud(volume=volume_at(cx), muted=False))

    def _set(self, hud: VolumeHud) -> None:
        # Both, always: muted and turned-all-the-way-down look the same drawn.
        self._hud = hud
        self._player.set_volume(hud.volume)
        self._player.set_muted(hud.muted)
