"""The controls drawn over a video: how big, what a squeeze does, when to repaint.

One angular size for every control in the scene, in the console's own pixels: in
the video's own a scrubber was a fifth of a degree tall on a satellite, too small
for a controller ray, and a fat bar on a zoomed main player.  They repaint only
when what they show moves; one per unit per pump tick cost the pump its time.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image
from player_core.funscript import Funscript
from player_core.playhead import on_readout, readout_xy
from player_core.timeline import TIMELINE_HEIGHT
from player_core.volume import VolumeHud, chip_local, chip_xy, hit_part, volume_at

from main_player.overlay import HeatmapStrip, timeline_bgra, timeline_x
from satellite.pointer import time_at

from .console_panel import DEG_PER_PX
from .pointer import Screen, surface_pixel

SCRUBBER = "scrubber"
READOUT = "readout"
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


def paint_row(
    bar: np.ndarray, playhead, hud: VolumeHud, size: tuple[int, int],
    *, volume_painter, readout_painter,
) -> np.ndarray:
    width, height = size  # a transparent strip: RGBA rows, top row first
    under = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    under.paste(Image.fromarray(bar, "RGBA"), (0, height - bar.shape[0]))
    over = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    over.paste(Image.fromarray(np.ascontiguousarray(volume_painter.bgra(hud)), "RGBA"),
               chip_xy(win_w=width, win_h=height, timeline_h=TIMELINE_HEIGHT))  # BGRA both: only the swap cares
    if playhead is not None:
        pill = readout_painter.bgra(playhead)
        over.paste(Image.fromarray(np.ascontiguousarray(pill), "RGBA"),
                   readout_xy(pill.shape[1], win_w=width, win_h=height, timeline_h=TIMELINE_HEIGHT))
    row = np.asarray(Image.alpha_composite(under, over))  # not with_furniture, which drops the alpha
    return np.ascontiguousarray(row[:, :, [2, 1, 0, 3]])


def furniture_at(u: float, v: float, *, size: tuple[int, int]) -> str | None:
    px, py = surface_pixel(u, v, size)
    part = hit_part(*chip_local(px, py, win_w=size[0], win_h=size[1],
                                timeline_h=TIMELINE_HEIGHT))
    if part:
        return _CHIP_PARTS[part]
    if on_readout(px, py, win_w=size[0], win_h=size[1], timeline_h=TIMELINE_HEIGHT):
        return READOUT
    return SCRUBBER if py >= size[1] - TIMELINE_HEIGHT else None


def on_its_controls(screen: Screen, u: float, v: float) -> bool:
    size = control_size(screen.placement.width_deg, screen.aspect)
    return furniture_at(u, v, size=size) is not None


def scrub_at(u: float, v: float, *, size: tuple[int, int], duration_ms: float) -> float:
    return time_at(surface_pixel(u, v, size)[0], win_w=size[0], duration_ms=duration_ms)


def volume_slid_to(u: float, v: float, *, size: tuple[int, int]) -> int:
    px, py = surface_pixel(u, v, size)
    cx, _cy = chip_local(px, py, win_w=size[0], win_h=size[1], timeline_h=TIMELINE_HEIGHT)
    return volume_at(cx)


class FurniturePointer:
    """What a squeeze on a video's own controls does: the scrubber seeks and keeps
    seeking as the hand moves, the speaker mutes, and the slider sets the level.
    A squeeze on none of them is a squeeze on the picture, which is *picture*'s."""

    def __init__(
        self, *, seek: Callable[[float], None], mute: Callable[[bool], None] | None = None,
        set_volume: Callable[[int], None] | None = None,
        picture: Callable[[], None] | None = None,
        picture_on_screen: Callable[[], bool] = lambda: False,
    ) -> None:
        self._seek = seek
        self._mute = mute
        self._set_volume = set_volume
        self._picture = picture
        self._picture_on_screen = picture_on_screen
        self._holding = ""
        self._asked = -1

    def press(
        self, u: float, v: float, *, size: tuple[int, int], duration_ms: float, muted: bool,
    ) -> None:
        self.release()
        part = furniture_at(u, v, size=size)
        if part == SCRUBBER and self._picture_on_screen():
            part = None
        if part == SCRUBBER:
            self._holding = SCRUBBER
            self._seek(scrub_at(u, v, size=size, duration_ms=duration_ms))
        elif part == MUTE and self._mute is not None:
            self._mute(muted)
        elif part == VOLUME and self._set_volume is not None:
            self._holding = VOLUME
            self._ask(volume_slid_to(u, v, size=size))
        elif part is None and self._picture is not None:
            self._picture()

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


class Scrubber:
    def __init__(self) -> None:
        self._strip = HeatmapStrip()

    def state(
        self, size: tuple[int, int], position_ms: float, duration_ms: float, *,
        video: Path | None = None, funscript: Funscript | None = None,
    ) -> tuple:
        self._strip.update(video, funscript, duration_ms, size[0])
        return size, self._strip.colors, timeline_x(self._strip, position_ms, size[0])

    def bgra(self, position_ms: float, width: int) -> np.ndarray:
        return timeline_bgra(self._strip, position_ms, None, width)


def chip_state(width: int, height: int, hud: VolumeHud) -> tuple[int, int, VolumeHud]:
    return (width, height, hud)
