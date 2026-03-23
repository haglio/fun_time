from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .loop_modes import (
    LOOP_MODE_BASE_TIP,
    LOOP_MODE_TIP_BASE,
    LOOP_MODE_TIP_BASE_TIP,
)

if TYPE_CHECKING:
    from .state import VideoState


def current_loop_frame_index(state: VideoState) -> int:
    sequence = loop_preview_indices(state)
    count = len(sequence)
    if count == 1:
        state.paused_loop_pos = 0
        state.paused_loop_idx = sequence[0]
        return sequence[0]
    if state.loop_paused:
        paused_pos = state.paused_loop_pos
        if paused_pos is None:
            paused_frame = state.paused_loop_idx if state.paused_loop_idx is not None else sequence[0]
            paused_pos = sequence.index(paused_frame) if paused_frame in sequence else 0
        paused_pos = max(0, min(count - 1, paused_pos))
        state.paused_loop_pos = paused_pos
        state.paused_loop_idx = sequence[paused_pos]
        return sequence[paused_pos]
    elapsed = time.monotonic() - state.loop_anchor
    offset = int(elapsed * state.fps * state.speed) % count
    idx = sequence[offset]
    state.paused_loop_pos = offset
    state.paused_loop_idx = idx
    return idx


def loop_preview_indices(state: VideoState) -> list[int]:
    forward = list(range(state.active_start, state.active_end + 1))
    if not forward:
        return [state.active_start]
    if state.loop_mode == LOOP_MODE_TIP_BASE_TIP:
        shift = max(1, len(forward) // 2)
        return forward[shift:] + forward[:shift]
    if state.loop_mode == LOOP_MODE_BASE_TIP:
        return forward + forward[-2::-1]
    if state.loop_mode == LOOP_MODE_TIP_BASE:
        backward = list(reversed(forward))
        return backward[:-1] + forward
    return forward


def change_speed(state: VideoState, delta: float) -> None:
    old_speed = state.speed
    _ = current_loop_frame_index(state)
    new_speed = max(0.25, min(2.0, round((state.speed + delta) * 4) / 4))
    if new_speed == old_speed:
        return
    offset = state.paused_loop_pos if state.paused_loop_pos is not None else 0
    state.speed = new_speed
    state.loop_anchor = time.monotonic() - (offset / max(1e-9, state.fps * state.speed))
    if not state.loop_paused:
        state.paused_loop_idx = None
        state.paused_loop_pos = None
    state.render_rev += 1


def toggle_loop_pause(state: VideoState) -> None:
    current_idx = current_loop_frame_index(state)
    current_pos = state.paused_loop_pos if state.paused_loop_pos is not None else 0
    if state.loop_paused:
        state.loop_paused = False
        state.paused_loop_idx = None
        state.paused_loop_pos = None
        state.loop_anchor = time.monotonic() - (current_pos / max(1e-9, state.fps * state.speed))
    else:
        state.loop_paused = True
        state.paused_loop_idx = current_idx
        state.paused_loop_pos = current_pos
    state.render_rev += 1
