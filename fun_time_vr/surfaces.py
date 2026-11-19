from __future__ import annotations

import threading
from typing import NamedTuple

import numpy as np
from funestra.flick_overlay import FlickFrame

from .texture_rows import Rows, rows_changed, rows_together


def red_and_blue_swapped(pixels: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(pixels[:, :, [2, 1, 0, 3]])


class HudChange(NamedTuple):
    bgra: np.ndarray | None
    version: int
    rows: Rows | None


class HudBitmap:
    def __init__(self, width: int | None = None) -> None:
        self.width = width
        self._lock = threading.Lock()
        self._bgra: np.ndarray | None = None
        self._unsent: Rows | None = None
        self._version = 0
        self._origin = (0, 0)

    def overlay(self, _ident: int, x: int, y: int, bgra: np.ndarray) -> None:
        before = self._bgra
        changed = Rows(0, len(bgra)) if bgra is before else rows_changed(before, bgra)
        with self._lock:
            self._bgra = bgra
            self._unsent = rows_together(self._unsent, changed)
            self._origin = (x, y)
            self._version += 1

    def remove_overlay(self, _ident: int) -> None:
        with self._lock:
            self._bgra = None
            self._unsent = None
            self._version += 1

    def take(self) -> HudChange:
        with self._lock:
            rows, self._unsent = self._unsent, None
            return HudChange(self._bgra, self._version, rows)

    @property
    def size(self) -> tuple[int, int] | None:
        with self._lock:
            if self._bgra is None:
                return None
            height, width = self._bgra.shape[:2]
            return width, height

    def in_the_window(self, px: int, py: int) -> tuple[int, int]:
        with self._lock:
            left, top = self._origin
        return left + px, top + py


class LatestFrame:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flick_frame: FlickFrame | None = None

    def show(self, flick_frame: FlickFrame, _viewport: tuple[int, int]) -> None:
        with self._lock:
            self._flick_frame = flick_frame

    def hide(self) -> None:
        with self._lock:
            self._flick_frame = None

    @property
    def flick_frame(self) -> FlickFrame | None:
        with self._lock:
            return self._flick_frame
