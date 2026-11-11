from __future__ import annotations

from .thumbs import CONTROLLER_DEADZONE

WHEEL_NOTCH = 120  # one notch, in the eighths of a degree Qt counts them in
_NOTCHES_PER_S = 8.0


def scroll_from_stick(axis: float, elapsed_s: float) -> float:
    if abs(axis) <= CONTROLLER_DEADZONE:
        return 0.0
    return axis * elapsed_s * _NOTCHES_PER_S * WHEEL_NOTCH
