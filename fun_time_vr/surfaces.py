"""The two surfaces a headset gives a Funestra: the panel as a bitmap to hang
beside the picture, and a User's own picture as a frame to hang in the slot."""
from __future__ import annotations

import threading

import numpy as np
from player_core.clip_picture import Picture


class PanelBitmap:
    def __init__(self, width: int | None = None) -> None:
        self.width = width
        self._lock = threading.Lock()
        self._rgba: np.ndarray | None = None
        self._version = 0
        self._origin = (0, 0)

    def overlay(self, _ident: int, x: int, y: int, bgra: np.ndarray) -> None:
        rgba = np.ascontiguousarray(bgra[:, :, [2, 1, 0, 3]])
        with self._lock:
            self._rgba = rgba
            self._origin = (x, y)
            self._version += 1

    def remove_overlay(self, _ident: int) -> None:
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

    def in_the_window(self, px: int, py: int) -> tuple[int, int]:
        with self._lock:
            left, top = self._origin
        return left + px, top + py


class LatestPicture:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._picture: Picture | None = None

    def show(self, picture: Picture, _window: tuple[int, int]) -> None:
        with self._lock:
            self._picture = picture

    def hide(self) -> None:
        with self._lock:
            self._picture = None

    @property
    def picture(self) -> Picture | None:
        with self._lock:
            return self._picture
