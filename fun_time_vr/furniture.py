"""The controls drawn over a video: how big, what a squeeze does, when to repaint.

One angular size for every control in the scene, in the console's own pixels: in
the video's own a scrubber was a fifth of a degree tall on a satellite, too small
for a controller ray, and a fat bar on a zoomed main player.  They repaint only
when what they show moves; one per unit per pump tick cost the pump its time.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
from PIL import Image
from player_core.timeline import TIMELINE_HEIGHT, bar_track_x, bar_x
from player_core.volume import VolumeHud, chip_local, hit_part, volume_at

from satellite.pointer import time_at

from .console_panel import DEG_PER_PX
from .pointer import surface_pixel

SCRUBBER = "scrubber"
MUTE = "mute"
VOLUME = "volume"

_CHIP_PARTS = {"mute": MUTE, "track": VOLUME}


def control_size(width_deg: float, aspect: float) -> tuple[int, int]:
    width = max(1, round(width_deg / DEG_PER_PX))  # the screen in console pixels
    return width, max(1, round(width / aspect))


def scaled(bgra: np.ndarray, factor: float) -> np.ndarray:
    height, width = bgra.shape[:2]  # NEAREST: these are UI bitmaps, not photographs
    image = Image.fromarray(np.ascontiguousarray(bgra), "RGBA").resize(
        (max(1, round(width * factor)), max(1, round(height * factor))), Image.NEAREST)
    return np.ascontiguousarray(np.asarray(image))


def with_furniture(frame: np.ndarray, pieces) -> np.ndarray:
    """*frame* with each ``(bitmap, x, y)`` blended over it, top-left origin -- a
    copy, since the engine reuses the one it handed over."""
    out = frame.copy()
    for bgra, x, y in pieces:
        height, width = bgra.shape[:2]
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(out.shape[1], x + width), min(out.shape[0], y + height)
        if x1 <= x0 or y1 <= y0:
            continue
        patch = bgra[y0 - y:y1 - y, x0 - x:x1 - x]
        alpha = patch[:, :, 3:4].astype(np.uint16)
        over = patch[:, :, 2::-1].astype(np.uint16)  # the bitmaps are BGRA
        under = out[y0:y1, x0:x1, :3].astype(np.uint16)
        out[y0:y1, x0:x1, :3] = ((over * alpha + under * (255 - alpha)) // 255).astype(np.uint8)
    return out


def furniture_at(u: float, v: float, *, size: tuple[int, int]) -> str | None:
    px, py = surface_pixel(u, v, size)
    part = hit_part(*chip_local(px, py, win_w=size[0], win_h=size[1],
                                timeline_h=TIMELINE_HEIGHT))
    if part:
        return _CHIP_PARTS[part]
    return SCRUBBER if py >= size[1] - TIMELINE_HEIGHT else None


def scrub_at(u: float, v: float, *, size: tuple[int, int], duration_ms: float) -> float:
    return time_at(surface_pixel(u, v, size)[0], win_w=size[0], duration_ms=duration_ms)


def volume_slid_to(u: float, v: float, *, size: tuple[int, int]) -> int:
    px, py = surface_pixel(u, v, size)
    cx, _cy = chip_local(px, py, win_w=size[0], win_h=size[1], timeline_h=TIMELINE_HEIGHT)
    return volume_at(cx)


class FurniturePointer:
    """What a squeeze on a video's own controls does: the scrubber seeks and keeps
    seeking as the hand moves, the speaker mutes, and the slider sets the level."""

    def __init__(
        self, *, seek: Callable[[float], None], mute: Callable[[bool], None] | None = None,
        set_volume: Callable[[int], None] | None = None,
    ) -> None:
        self._seek = seek
        self._mute = mute
        self._set_volume = set_volume
        self._holding = ""
        self._asked = -1

    def press(
        self, u: float, v: float, *, size: tuple[int, int], duration_ms: float, muted: bool,
    ) -> None:
        self.release()
        part = furniture_at(u, v, size=size)
        if part == SCRUBBER:
            self._holding = SCRUBBER
            self._seek(scrub_at(u, v, size=size, duration_ms=duration_ms))
        elif part == MUTE and self._mute is not None:
            self._mute(muted)
        elif part == VOLUME and self._set_volume is not None:
            self._holding = VOLUME
            self._ask(volume_slid_to(u, v, size=size))

    def drag(self, u: float, v: float, *, size: tuple[int, int], duration_ms: float) -> None:
        if self._holding == SCRUBBER:
            self._seek(scrub_at(u, v, size=size, duration_ms=duration_ms))
        elif self._holding == VOLUME:
            self._ask(volume_slid_to(u, v, size=size))

    def release(self) -> None:
        self._holding, self._asked = "", -1

    def _ask(self, level: int) -> None:
        if level != self._asked:
            self._asked = level
            self._set_volume(level)


def scrubber_state(
    width: int, height: int, position_ms: float, duration_ms: float
) -> tuple[int, int, int]:
    """What the bar depends on: it is identical until the cursor crosses a pixel."""
    x0, x1 = bar_track_x(width)
    return (width, height, bar_x(position_ms, duration_ms, x0, x1))


def chip_state(width: int, height: int, hud: VolumeHud) -> tuple[int, int, VolumeHud]:
    return (width, height, hud)
