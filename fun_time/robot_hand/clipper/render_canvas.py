from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable, Any

import cv2
import numpy as np

from .loop_modes import LOOP_MODE_LABELS
from .navigation import timeline_x_for_index
from .playback import loop_preview_indices
from .utils import format_seconds

if TYPE_CHECKING:
    from .state import VideoState


def build_canvas(
    state: VideoState,
    current_frame: np.ndarray,
    loop_frame: np.ndarray,
    loop_idx: int,
    *,
    draw_button: Callable[..., Any],
    draw_dotted_vertical_line: Callable[..., Any],
    draw_exit_overlay: Callable[..., Any],
    draw_export_overlay: Callable[..., Any],
    put_text: Callable[..., Any],
    put_text_centered: Callable[..., Any],
    scale_to_fit: Callable[..., np.ndarray],
) -> np.ndarray:
    state.buttons = {}

    pane_w = 720
    pane_h = 500
    canvas_w = 1520
    canvas_h = 1040
    margin = 18
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    canvas[:] = (24, 24, 24)

    loaded_color = (82, 64, 46)
    active_color = (176, 155, 116)

    left_x = margin
    right_x = canvas_w - margin - pane_w
    session_cx = canvas_w // 2

    session_y = 34
    meta_y = 66
    title_y = 122
    pane_y = 144
    pane_bottom = pane_y + pane_h
    info1_y = pane_bottom + 28
    info2_y = pane_bottom + 58
    timeline_y = 772
    timeline_h = 22
    shift_y = timeline_y - 48
    mark_y = timeline_y + timeline_h + 18
    wrap_y = mark_y + 54
    mode_y = wrap_y + 44
    range_info_y = mode_y + 48
    legend_y1 = range_info_y + 42
    legend_y2 = legend_y1 + 30

    put_text_centered(canvas, state.session_name, session_cx, session_y, 0.92, (240, 240, 240), 2)
    meta_text = f"File: {os.path.basename(state.path)}     FPS: {state.fps:.3f}"
    put_text_centered(canvas, meta_text, session_cx, meta_y, 0.58, (230, 230, 230), 1)

    left_cx = left_x + pane_w // 2
    right_cx = right_x + pane_w // 2
    put_text_centered(canvas, "Frame at cursor", left_cx, title_y, 0.9, (240, 240, 240), 2)
    put_text_centered(canvas, "Loop preview", right_cx, title_y, 0.9, (240, 240, 240), 2)

    cv2.rectangle(canvas, (left_x, pane_y), (left_x + pane_w, pane_y + pane_h), (40, 40, 40), -1)
    cv2.rectangle(canvas, (right_x, pane_y), (right_x + pane_w, pane_y + pane_h), (40, 40, 40), -1)

    nav_view = scale_to_fit(current_frame, pane_w, pane_h)
    loop_view = scale_to_fit(loop_frame, pane_w, pane_h)
    nav_x = left_x + (pane_w - nav_view.shape[1]) // 2
    nav_y = pane_y + (pane_h - nav_view.shape[0]) // 2
    loop_x = right_x + (pane_w - loop_view.shape[1]) // 2
    loop_y = pane_y + (pane_h - loop_view.shape[0]) // 2
    canvas[nav_y:nav_y + nav_view.shape[0], nav_x:nav_x + nav_view.shape[1]] = nav_view
    canvas[loop_y:loop_y + loop_view.shape[0], loop_x:loop_x + loop_view.shape[1]] = loop_view

    rel_cur = state.current - state.loaded_start
    put_text(canvas, f"Cursor: {rel_cur} @ {format_seconds(state.current / state.fps)}", left_x, info1_y, 0.58, (235, 235, 235), 1)
    preview_sequence = loop_preview_indices(state)
    preview_pos = state.paused_loop_pos if state.paused_loop_pos is not None else (preview_sequence.index(loop_idx) if loop_idx in preview_sequence else 0)
    put_text(canvas, f"Loop frame: {preview_pos} @ {format_seconds(loop_idx / state.fps)}", right_x, info1_y, 0.58, (235, 235, 235), 1)
    playback_status = "Paused" if state.loop_paused else "Playing"
    put_text(canvas, f"Speed: {state.speed:.2f}x ({playback_status})", right_x, info2_y, 0.58, (235, 235, 235), 1)

    b_h = 34
    control_w = b_h
    export_w = 120
    gap = 10
    yb = info1_y - 22
    bx3 = right_x + pane_w - export_w
    bxp = bx3 - gap - control_w
    bx2 = bxp - gap - control_w
    bx1 = bx2 - gap - control_w
    state.buttons["speed_down"] = (bx1, yb, bx1 + control_w, yb + b_h)
    state.buttons["speed_up"] = (bx2, yb, bx2 + control_w, yb + b_h)
    state.buttons["play_pause"] = (bxp, yb, bxp + control_w, yb + b_h)
    state.buttons["export"] = (bx3, yb, bx3 + export_w, yb + b_h)
    draw_button(canvas, state.buttons["speed_down"], "-")
    draw_button(canvas, state.buttons["speed_up"], "+")
    draw_button(canvas, state.buttons["play_pause"], "", icon="play" if state.loop_paused else "pause")
    draw_button(canvas, state.buttons["export"], "Export")

    btn_w = 44
    btn_h = 34
    tl_x1 = 150
    tl_x2 = canvas_w - 150
    left_btn_y = timeline_y + timeline_h // 2 - btn_h // 2
    left_buttons_x = 20
    right_buttons_x = canvas_w - 20 - 2 * btn_w - 8
    state.buttons["extend_left"] = (left_buttons_x, left_btn_y, left_buttons_x + btn_w, left_btn_y + btn_h)
    state.buttons["contract_left"] = (left_buttons_x + btn_w + 8, left_btn_y, left_buttons_x + 2 * btn_w + 8, left_btn_y + btn_h)
    state.buttons["contract_right"] = (right_buttons_x, left_btn_y, right_buttons_x + btn_w, left_btn_y + btn_h)
    state.buttons["extend_right"] = (right_buttons_x + btn_w + 8, left_btn_y, right_buttons_x + 2 * btn_w + 8, left_btn_y + btn_h)
    draw_button(canvas, state.buttons["extend_left"], "<", enabled=state.loaded_start > 0)
    draw_button(canvas, state.buttons["contract_left"], ">", enabled=(state.active_start - state.loaded_start) >= state.base_step)
    draw_button(canvas, state.buttons["contract_right"], "<", enabled=(state.loaded_end - state.active_end) >= state.base_step)
    draw_button(canvas, state.buttons["extend_right"], ">", enabled=state.loaded_end < state.total_frames - 1)

    cv2.rectangle(canvas, (tl_x1, timeline_y), (tl_x2, timeline_y + timeline_h), loaded_color, -1)
    in_x = timeline_x_for_index(state, tl_x1, tl_x2, state.active_start)
    out_x = timeline_x_for_index(state, tl_x1, tl_x2, state.active_end)
    cv2.rectangle(canvas, (in_x, timeline_y), (out_x, timeline_y + timeline_h), active_color, -1)
    cur_x = timeline_x_for_index(state, tl_x1, tl_x2, state.current)
    loop_x_t = timeline_x_for_index(state, tl_x1, tl_x2, loop_idx)
    if state.suggested_in is not None:
        sugg_in_x = timeline_x_for_index(state, tl_x1, tl_x2, state.suggested_in)
        draw_dotted_vertical_line(canvas, sugg_in_x, timeline_y - 12, timeline_y + timeline_h + 12, (90, 220, 255), thickness=2)
    if state.suggested_out is not None:
        sugg_out_x = timeline_x_for_index(state, tl_x1, tl_x2, state.suggested_out)
        draw_dotted_vertical_line(canvas, sugg_out_x, timeline_y - 12, timeline_y + timeline_h + 12, (255, 210, 90), thickness=2)
    cv2.rectangle(canvas, (cur_x - 1, timeline_y - 4), (cur_x + 1, timeline_y + timeline_h + 4), (255, 255, 255), -1)
    cv2.rectangle(canvas, (loop_x_t - 1, timeline_y - 4), (loop_x_t + 1, timeline_y + timeline_h + 4), (50, 50, 255), -1)
    cv2.rectangle(canvas, (tl_x1, timeline_y), (tl_x2, timeline_y + timeline_h), (220, 220, 220), 1)
    state.buttons["timeline"] = (tl_x1, timeline_y - 8, tl_x2, timeline_y + timeline_h + 8)

    shift_gap = 8
    shift_left_enabled = state.active_start - (state.active_end - state.active_start) >= 0
    shift_right_enabled = state.active_end + (state.active_end - state.active_start) < state.total_frames
    shift_center = (in_x + out_x) // 2
    state.buttons["shift_left"] = (shift_center - shift_gap // 2 - btn_w, shift_y, shift_center - shift_gap // 2, shift_y + btn_h)
    state.buttons["shift_right"] = (shift_center + shift_gap // 2, shift_y, shift_center + shift_gap // 2 + btn_w, shift_y + btn_h)
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

    rel_in = state.active_start - state.loaded_start
    rel_out = state.active_end - state.loaded_start
    wrap_label = "Wrap: Loaded" if state.wrap_mode == "blue" else "Wrap: In-Out"
    range_text = f"In-Out: {rel_in}-{rel_out}     Loaded: 0-{state.loaded_count - 1}     {wrap_label}"
    put_text_centered(canvas, range_text, session_cx, range_info_y, 0.58, (230, 230, 230), 1)

    legend1 = "Left/Right: Move cursor   A/S/D/F: Loaded bounds   Space: Play/Pause preview   Enter: Export"
    legend2 = "i or [: Mark In   o or ]: Mark Out   < or >: Shift In-Out   (: Accept In suggestion   ): Accept Out suggestion   M: Wrap   L: Loop mode   -/+: Speed"
    put_text_centered(canvas, legend1, session_cx, legend_y1, 0.56, (230, 230, 230), 1)
    put_text_centered(canvas, legend2, session_cx, legend_y2, 0.56, (230, 230, 230), 1)
    if state.session_warning:
        put_text_centered(canvas, state.session_warning, session_cx, legend_y1 - 28, 0.52, (120, 200, 255), 1)

    if state.export_job and not state.export_job.dismissed:
        draw_export_overlay(canvas, state)
    if state.exit_prompt_visible:
        draw_exit_overlay(canvas, state)

    return canvas
