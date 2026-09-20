from __future__ import annotations

import numpy as np

FISHEYE_CIRCLE = "fisheye_circle"
FULL_FRAME = "full_frame"

LIT_ABOVE = 16
MOSTLY = 0.8
# A fisheye's circle touches its eye's square, so the part of the square just
# past the circle is black; a studio's mark, where there is one, sits further
# out in the corner.
_JUST_PAST_THE_CIRCLE = (1.05, 1.25)
# The ring a fisheye's picture still reaches whatever its circle's exact size, and
# a dim scene fading toward its edges does not.
_JUST_INSIDE_THE_CIRCLE = (0.80, 0.93)


def shape_of(luma: np.ndarray) -> str | None:
    middle = luma.shape[1] // 2
    left, right = _eye_shape(luma[:, :middle]), _eye_shape(luma[:, middle:])
    return left if left == right else None


def _eye_shape(eye: np.ndarray) -> str | None:
    out = _distance_out(*eye.shape)
    if _lit(eye, out, _JUST_INSIDE_THE_CIRCLE) < MOSTLY:
        return None
    past_the_circle = _lit(eye, out, _JUST_PAST_THE_CIRCLE)
    if past_the_circle < 1 - MOSTLY:
        return FISHEYE_CIRCLE
    return FULL_FRAME if past_the_circle > MOSTLY else None


def _distance_out(height: int, width: int) -> np.ndarray:
    ys = (np.arange(height) + 0.5) / height * 2 - 1
    xs = (np.arange(width) + 0.5) / width * 2 - 1
    return np.hypot(*np.meshgrid(xs, ys))


def _lit(eye: np.ndarray, out: np.ndarray, ring: tuple[float, float]) -> float:
    nearer, further = ring
    return float((eye[(out > nearer) & (out < further)] > LIT_ABOVE).mean())
