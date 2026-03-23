from __future__ import annotations

from typing import TYPE_CHECKING

from .frame_store import ensure_loaded, prune_loaded_caches
from .state import update_loop_suggestions

if TYPE_CHECKING:
    from .state import VideoState


def contract_left(state: VideoState) -> None:
    if state.active_start - state.loaded_start >= state.base_step:
        state.loaded_start += state.base_step
        prune_loaded_caches(state)
        state.current = max(state.current, state.loaded_start)
        update_loop_suggestions(state)
        state.mark_dirty()


def extend_left(state: VideoState) -> None:
    new_start = max(0, state.loaded_start - state.base_step)
    ensure_loaded(state, new_start, state.loaded_end)
    if new_start != state.loaded_start:
        state.loaded_start = new_start
    update_loop_suggestions(state)
    state.mark_dirty()


def contract_right(state: VideoState) -> None:
    if state.loaded_end - state.active_end >= state.base_step:
        state.loaded_end -= state.base_step
        prune_loaded_caches(state)
        state.current = min(state.current, state.loaded_end)
        update_loop_suggestions(state)
        state.mark_dirty()


def extend_right(state: VideoState) -> None:
    new_end = min(state.total_frames - 1, state.loaded_end + state.base_step)
    ensure_loaded(state, state.loaded_start, new_end)
    if new_end != state.loaded_end:
        state.loaded_end = new_end
    update_loop_suggestions(state)
    state.mark_dirty()
