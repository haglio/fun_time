from __future__ import annotations

from funestra.funestra_controls import SEEK_STEP_MS
from funestra.funestra_verbs import SEEK_BACK, SEEK_FWD
from funestra.status import FunestraStatus


def longer_than_a_step(duration_ms: float) -> bool:
    return duration_ms > SEEK_STEP_MS


def too_short_to_step(noun: str) -> str:
    return f"this {noun} is 10s or shorter"


def too_near_the_start_to_step_back(noun: str) -> str:
    return f"less than 10s into this {noun}"


def too_near_the_end_to_step_forward(noun: str) -> str:
    return f"less than 10s left in this {noun}"


def why_it_cannot_step(verb: str, status: FunestraStatus, noun: str) -> str | None:
    if not longer_than_a_step(status.duration_ms):
        return too_short_to_step(noun)
    if verb == SEEK_BACK and status.position_ms < SEEK_STEP_MS:
        return too_near_the_start_to_step_back(noun)
    if verb == SEEK_FWD and not longer_than_a_step(status.duration_ms - status.position_ms):
        return too_near_the_end_to_step_forward(noun)
    return None
