from __future__ import annotations

from typing import TYPE_CHECKING

from .frame_store import ensure_loaded
from .loop_modes import LOOP_MODES
from .state import update_loop_suggestions

if TYPE_CHECKING:
    from .state import VideoState


def set_mark_in(state: VideoState) -> None:
    if state.current < state.active_end:
        state.active_start = state.current
        state.suggestion_anchor_in = state.active_start
        state.reset_loop_anchor()
        update_loop_suggestions(state)
        state.mark_dirty()


def set_mark_out(state: VideoState) -> None:
    if state.current > state.active_start:
        state.active_end = state.current
        state.suggestion_anchor_out = state.active_end
        state.reset_loop_anchor()
        update_loop_suggestions(state)
        state.mark_dirty()


def accept_suggested_in(state: VideoState) -> None:
    if state.suggested_in is None or state.suggested_in >= state.active_end:
        return
    state.active_start = state.suggested_in
    state.suggestion_anchor_in = state.active_start
    state.reset_loop_anchor()
    update_loop_suggestions(state)
    state.mark_dirty()


def accept_suggested_out(state: VideoState) -> None:
    if state.suggested_out is None or state.suggested_out <= state.active_start:
        return
    state.active_end = state.suggested_out
    state.suggestion_anchor_out = state.active_end
    state.reset_loop_anchor()
    update_loop_suggestions(state)
    state.mark_dirty()


def shift_active_range(state: VideoState, direction: int) -> None:
    if direction == 0:
        return
    shift = (state.active_end - state.active_start) * (1 if direction > 0 else -1)
    if shift == 0:
        return

    new_start = state.active_start + shift
    new_end = state.active_end + shift
    if new_start < 0 or new_end >= state.total_frames:
        return

    want_start = new_start
    want_end = new_end
    if direction > 0:
        want_end = min(state.total_frames - 1, new_end + state.base_step)
    else:
        want_start = max(0, new_start - state.base_step)

    ensure_loaded(state, want_start, want_end)
    state.active_start = new_start
    state.active_end = new_end
    state.suggestion_anchor_in = state.active_start
    state.suggestion_anchor_out = state.active_end
    state.current += shift
    state.clamp_current()
    state.reset_loop_anchor()
    update_loop_suggestions(state)
    state.mark_dirty()


def cycle_loop_mode(state: VideoState, step: int = 1) -> None:
    current_idx = LOOP_MODES.index(state.loop_mode) if state.loop_mode in LOOP_MODES else 0
    state.loop_mode = LOOP_MODES[(current_idx + step) % len(LOOP_MODES)]
    state.mark_dirty()
