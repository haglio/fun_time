from __future__ import annotations

from .projection import (
    EQUIRECT_180_SBS,
    EQUIRECT_360,
    FISHEYE_180_SBS,
    FISHEYE_190_SBS,
    FISHEYE_200_EQUISOLID_SBS,
    FISHEYE_200_STEREOGRAPHIC_SBS,
    FISHEYE_220_SBS,
    FLAT,
    MKX200_SBS,
    RECTILINEAR_SBS,
)
from .render import Wrap

READOUT_EVERY_S = 0.1

_LABELS = {
    FLAT: "Flat",
    EQUIRECT_180_SBS: "180° SBS",
    FISHEYE_180_SBS: "Fisheye",
    FISHEYE_200_STEREOGRAPHIC_SBS: "Stereographic",
    FISHEYE_200_EQUISOLID_SBS: "Equisolid",
    RECTILINEAR_SBS: "Rectilinear",
    EQUIRECT_360: "360°",
    FISHEYE_190_SBS: "Fisheye 190",
    MKX200_SBS: "MKX200",
    FISHEYE_220_SBS: "Fisheye 220",
}


def label(projection: str) -> str:
    return _LABELS.get(projection, projection)


def readout(projection: str, wrap: Wrap | None) -> str:
    if wrap is None or not wrap.fov_deg:
        return label(projection)
    return f"{label(projection)} · {wrap.fov_deg:.0f}° · height {wrap.height:.2f}"


class WrapReadout:
    def __init__(self) -> None:
        self._shown: str | None = None
        self._flashed_at: float | None = None

    def frame(self, text: str | None, *, now: float) -> str | None:
        if text is None:
            self._shown = None
            return None
        if text == self._shown or (
                self._flashed_at is not None and now - self._flashed_at < READOUT_EVERY_S):
            return None
        self._shown, self._flashed_at = text, now
        return text
