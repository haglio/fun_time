from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

import cv2
import numpy as np

from .loop_modes import LOOP_MODE_LABELS
from .navigation import timeline_x_for_index

if TYPE_CHECKING:
    from .state import VideoState


def draw_timeline_section(
    canvas: np.ndarray,
    state: VideoState,
    loop_idx: int,
    *,
    canvas_w: int,
    timeline_y: int,
    timeline_h: int,
    shift_y: int,
    mark_y: int,
    wrap_y: int,
    mode_y: int,
    session_cx: int,
    loaded_color: tuple[int, int, int],
    active_color: tuple[int, int, int],
    draw_button: Callable[..., Any],
    draw_dotted_vertical_line: Callable[..., Any],
) -> None:
    btn_w = 44
    btn_h = 34
    tl_x1 = 150
    tl_x2 = canvas_w - 150
    left_btn_y = timeline_y + timeline_h // 2 - btn_h // 2
    left_buttons_x = 20
    right_buttons_x = canvas_w - 20 - 2 * btn_w - 8
    state.buttons["extend_left"] = (left_buttons_x, left_btn_y, left_buttons_x + btn_w, left_btn_y + btn_h)
    state.buttons["contract_left"] = (
        left_buttons_x + btn_w + 8,
        left_btn_y,
        left_buttons_x + 2 * btn_w + 8,
        left_btn_y + btn_h,
    )
    state.buttons["contract_right"] = (right_buttons_x, left_btn_y, right_buttons_x + btn_w, left_btn_y + btn_h)
    state.buttons["extend_right"] = (
        right_buttons_x + btn_w + 8,
        left_btn_y,
        right_buttons_x + 2 * btn_w + 8,
        left_btn_y + btn_h,
    )
    draw_button(canvas, state.buttons["extend_left"], "<", enabled=state.loaded_start > 0)
    draw_button(
        canvas,
        state.buttons["contract_left"],
        ">",
        enabled=(state.active_start - state.loaded_start) >= state.base_step,
    )
    draw_button(
        canvas,
        state.buttons["contract_right"],
        "<",
        enabled=(state.loaded_end - state.active_end) >= state.base_step,
    )
    draw_button(canvas, state.buttons["extend_right"], ">", enabled=state.loaded_end < state.total_frames - 1)

    cv2.rectangle(canvas, (tl_x1, timeline_y), (tl_x2, timeline_y + timeline_h), loaded_color, -1)
    in_x = timeline_x_for_index(state, tl_x1, tl_x2, state.active_start)
    out_x = timeline_x_for_index(state, tl_x1, tl_x2, state.active_end)
    cv2.rectangle(canvas, (in_x, timeline_y), (out_x, timeline_y + timeline_h), active_color, -1)
    cur_x = timeline_x_for_index(state, tl_x1, tl_x2, state.current)
    loop_x_t = timeline_x_for_index(state, tl_x1, tl_x2, loop_idx)
    if state.suggested_in is not None:
        sugg_in_x = timeline_x_for_index(state, tl_x1, tl_x2, state.suggested_in)
        draw_dotted_vertical_line(
            canvas, sugg_in_x, timeline_y - 12, timeline_y + timeline_h + 12, (90, 220, 255), thickness=2
        )
    if state.suggested_out is not None:
        sugg_out_x = timeline_x_for_index(state, tl_x1, tl_x2, state.suggested_out)
        draw_dotted_vertical_line(
            canvas, sugg_out_x, timeline_y - 12, timeline_y + timeline_h + 12, (255, 210, 90), thickness=2
        )
    cv2.rectangle(canvas, (cur_x - 1, timeline_y - 4), (cur_x + 1, timeline_y + timeline_h + 4), (255, 255, 255), -1)
    cv2.rectangle(canvas, (loop_x_t - 1, timeline_y - 4), (loop_x_t + 1, timeline_y + timeline_h + 4), (50, 50, 255), -1)
    cv2.rectangle(canvas, (tl_x1, timeline_y), (tl_x2, timeline_y + timeline_h), (220, 220, 220), 1)
    state.buttons["timeline"] = (tl_x1, timeline_y - 8, tl_x2, timeline_y + timeline_h + 8)

    shift_gap = 8
    shift_left_enabled = state.active_start - (state.active_end - state.active_start) >= 0
    shift_right_enabled = state.active_end + (state.active_end - state.active_start) < state.total_frames
    shift_center = (in_x + out_x) // 2
    state.buttons["shift_left"] = (shift_center - shift_gap // 2 - btn_w, shift_y, shift_center - shift_gap // 2, shift_y + btn_h)
    state.buttons["shift_right"] = (
        shift_center + shift_gap // 2,
        shift_y,
        shift_center + shift_gap // 2 + btn_w,
        shift_y + btn_h,
    )
    draw_button(canvas, state.buttons["shift_left"], "<", enabled=shift_left_enabled)
    draw_button(canvas, state.buttons["shift_right"], ">", enabled=shift_right_enabled)

    enable_in = state.current < state.active_end
    enable_out = state.current > state.active_start
    cursor_x = cur_x
    mbw = 36
    mgap = 8
    if state.current < state.active_start:
        left_c = timeline_x_for_index(state, tl_x1, tl_x2, state.active_start)
        state.buttons["mark_in"] = (cursor_x - mbw // 2, mark_y, cursor_x + mbw // 2, mark_y + 32)
        state.buttons["mark_out"] = (left_c - mbw // 2, mark_y, left_c + mbw // 2, mark_y + 32)
    elif state.current > state.active_end:
        right_c = timeline_x_for_index(state, tl_x1, tl_x2, state.active_end)
        state.buttons["mark_in"] = (right_c - mbw // 2, mark_y, right_c + mbw // 2, mark_y + 32)
        state.buttons["mark_out"] = (cursor_x - mbw // 2, mark_y, cursor_x + mbw // 2, mark_y + 32)
    else:
        center = cursor_x
        state.buttons["mark_in"] = (center - mgap // 2 - mbw, mark_y, center - mgap // 2, mark_y + 32)
        state.buttons["mark_out"] = (center + mgap // 2, mark_y, center + mgap // 2 + mbw, mark_y + 32)
    draw_button(canvas, state.buttons["mark_in"], "[", enabled=enable_in)
    draw_button(canvas, state.buttons["mark_out"], "]", enabled=enable_out)

    wrap_lo = state.loaded_start if state.wrap_mode == "blue" else state.active_start
    wrap_hi = state.loaded_end if state.wrap_mode == "blue" else state.active_end
    wrap_x1 = timeline_x_for_index(state, tl_x1, tl_x2, wrap_lo)
    wrap_x2 = timeline_x_for_index(state, tl_x1, tl_x2, wrap_hi)
    cv2.line(canvas, (wrap_x1, wrap_y), (wrap_x2, wrap_y), (200, 200, 200), 1)
    cv2.line(canvas, (wrap_x1, wrap_y - 8), (wrap_x1, wrap_y + 8), (200, 200, 200), 1)
    cv2.line(canvas, (wrap_x2, wrap_y - 8), (wrap_x2, wrap_y + 8), (200, 200, 200), 1)
    wrap_w = 120
    wrap_c = (wrap_x1 + wrap_x2) // 2
    state.buttons["wrap"] = (wrap_c - wrap_w // 2, wrap_y - 16, wrap_c + wrap_w // 2, wrap_y + 16)
    draw_button(
        canvas,
        state.buttons["wrap"],
        "Wrap",
        active=True,
        fill_color=loaded_color,
        active_fill_color=active_color if state.wrap_mode == "yellow" else loaded_color,
    )

    loop_mode_w = 250
    state.buttons["loop_mode"] = (session_cx - loop_mode_w // 2, mode_y - 18, session_cx + loop_mode_w // 2, mode_y + 16)
    draw_button(canvas, state.buttons["loop_mode"], LOOP_MODE_LABELS.get(state.loop_mode, state.loop_mode))
