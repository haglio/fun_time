from __future__ import annotations

from typing import NamedTuple

import numpy as np


class Rows(NamedTuple):
    top: int
    bottom: int


def rows_changed(before: np.ndarray | None, after: np.ndarray) -> Rows | None:
    if before is None or before.shape != after.shape:
        return Rows(0, after.shape[0])
    differs = np.flatnonzero((before.reshape(len(before), -1) != after.reshape(len(after), -1)).any(axis=1))
    if not differs.size:
        return None
    return Rows(int(differs[0]), int(differs[-1]) + 1)


def rows_together(unsent: Rows | None, more: Rows | None) -> Rows | None:
    if unsent is None or more is None:
        return more or unsent
    return Rows(min(unsent.top, more.top), max(unsent.bottom, more.bottom))


def as_the_texture_holds_them(pixels: np.ndarray, rows: Rows) -> tuple[int, np.ndarray]:
    return len(pixels) - rows.bottom, pixels[rows.top:rows.bottom][::-1]
