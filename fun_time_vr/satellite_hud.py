"""A satellite's lock HUD in the headset: hung under its picture, pressed by the controller."""
from __future__ import annotations

import threading
from collections.abc import Callable

import numpy as np
from player_core.satellite_hud import MARGIN
from player_core.timeline import TIMELINE_HEIGHT

from satellite.pointer import time_at

from .pointer import surface_pixel

PICTURE = "picture"
HUD = "hud"
HUD_GAP_DEG = 0.6


def hud_screen_name(side: str) -> str:
    return f"{side}/{HUD}"


def screen_kind(name: str) -> str:
    return HUD if name.endswith(f"/{HUD}") else PICTURE


class HudSurface:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rgba: np.ndarray | None = None
        self._version = 0

    def overlay(self, _overlay_id: int, _x: int, _y: int, bgra: np.ndarray) -> None:
        rgba = np.ascontiguousarray(bgra[:, :, [2, 1, 0, 3]])
        with self._lock:
            self._rgba = rgba
            self._version += 1

    def remove_overlay(self, _overlay_id: int) -> None:
        with self._lock:
            self._rgba = None
            self._version += 1

    def take(self) -> tuple[np.ndarray | None, int]:
        with self._lock:
            return self._rgba, self._version

    @property
    def size(self) -> tuple[int, int] | None:
        with self._lock:
            if self._rgba is None:
                return None
            height, width = self._rgba.shape[:2]
            return width, height


class SatellitePointer:
    def __init__(
        self, *, hud, seek: Callable[[float], None], duration_ms: Callable[[], float],
    ) -> None:
        self._hud = hud
        self._seek = seek
        self._duration_ms = duration_ms

    def press(self, kind: str, u: float, v: float, *, size: tuple[int, int]) -> None:
        px, py = surface_pixel(u, v, size)
        if kind == HUD:
            self._hud.press(px + MARGIN, py + MARGIN)
        elif py >= size[1] - TIMELINE_HEIGHT:
            self._seek(time_at(px, win_w=size[0], duration_ms=self._duration_ms()))

    def hover(self, kind: str, uv: tuple[float, float] | None, *, size: tuple[int, int]) -> None:
        if kind == HUD and uv is not None:
            px, py = surface_pixel(*uv, size)
            self._hud.motion(px + MARGIN, py + MARGIN)
        else:
            self._hud.motion(-1, -1)
