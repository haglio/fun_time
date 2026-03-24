from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable, Any

import cv2
import numpy as np

from .playback import loop_preview_indices
from .render_timeline import draw_timeline_section
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

    draw_timeline_section(
        canvas,
        state,
        loop_idx,
        canvas_w=canvas_w,
        timeline_y=timeline_y,
        timeline_h=timeline_h,
        shift_y=shift_y,
        mark_y=mark_y,
        wrap_y=wrap_y,
        mode_y=mode_y,
        session_cx=session_cx,
        loaded_color=loaded_color,
        active_color=active_color,
        draw_button=draw_button,
        draw_dotted_vertical_line=draw_dotted_vertical_line,
    )

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
