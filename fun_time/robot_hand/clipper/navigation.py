from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .state import VideoState


def timeline_x_for_index(state: VideoState, x1: int, x2: int, idx: int) -> int:
    count = max(1, state.loaded_count - 1)
    frac = (idx - state.loaded_start) / count
    return int(round(x1 + frac * (x2 - x1)))


def index_for_timeline_x(state: VideoState, x1: int, x2: int, x: int) -> int:
    x = max(x1, min(x2, x))
    frac = 0.0 if x2 <= x1 else (x - x1) / (x2 - x1)
    idx = state.loaded_start + int(round(frac * max(1, state.loaded_count - 1)))
    return max(state.loaded_start, min(state.loaded_end, idx))


def toggle_wrap_mode(state: VideoState) -> None:
    state.wrap_mode = "yellow" if state.wrap_mode == "blue" else "blue"
    if state.wrap_mode == "yellow":
        state.current = max(state.active_start, min(state.active_end, state.current))
    state.mark_dirty()


def move_current_left(state: VideoState) -> None:
    low = state.loaded_start if state.wrap_mode == "blue" else state.active_start
    high = state.loaded_end if state.wrap_mode == "blue" else state.active_end
    if state.current <= low:
        state.current = high
    else:
        state.current -= 1
    state.render_rev += 1


def move_current_right(state: VideoState) -> None:
    low = state.loaded_start if state.wrap_mode == "blue" else state.active_start
    high = state.loaded_end if state.wrap_mode == "blue" else state.active_end
    if state.current >= high:
        state.current = low
    else:
        state.current += 1
    state.render_rev += 1
