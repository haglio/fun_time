from __future__ import annotations

import numpy as np

FISHEYE_CIRCLE = "fisheye_circle"
FULL_FRAME = "full_frame"

LIT_ABOVE = 16
MOSTLY = 0.8
_JUST_PAST_THE_CIRCLE = (1.05, 1.25)
_JUST_INSIDE_THE_CIRCLE = (0.80, 0.93)


def shape_of(luma: np.ndarray) -> str | None:
    middle = luma.shape[1] // 2
    left, right = _eye_shape(luma[:, :middle]), _eye_shape(luma[:, middle:])
    a_recordings_two_eyes_match = left == right
    return left if a_recordings_two_eyes_match else None


def _eye_shape(eye: np.ndarray) -> str | None:
    out = _in_radii_of_the_inscribed_circle(*eye.shape)
    if _lit(eye, out, _JUST_INSIDE_THE_CIRCLE) < MOSTLY:
        return None
    past_the_circle = _lit(eye, out, _JUST_PAST_THE_CIRCLE)
    if past_the_circle < 1 - MOSTLY:
        return FISHEYE_CIRCLE
    past_the_circle_neither_black_nor_lit = past_the_circle <= MOSTLY
    return None if past_the_circle_neither_black_nor_lit else FULL_FRAME


def _in_radii_of_the_inscribed_circle(height: int, width: int) -> np.ndarray:
    ys = (np.arange(height) + 0.5) / height * 2 - 1
    xs = (np.arange(width) + 0.5) / width * 2 - 1
    return np.hypot(*np.meshgrid(xs, ys))


def _lit(eye: np.ndarray, out: np.ndarray, ring: tuple[float, float]) -> float:
    nearer, further = ring
    return float((eye[(out > nearer) & (out < further)] > LIT_ABOVE).mean())
