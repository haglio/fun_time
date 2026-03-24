from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable, Any

import cv2
import numpy as np

from .playback import loop_preview_indices
from .render_primitives import text_bbox, text_wh
from .render_timeline import draw_timeline_section
from .utils import format_seconds

if TYPE_CHECKING:
    from .state import VideoState


LegendEntry = tuple[tuple[str, ...], str, str]

HOTKEY_LEGEND_ROWS: tuple[tuple[LegendEntry, ...], ...] = (
    (
        (("-", "+"), " or ", "speed"),
        (("space",), "", "play or pause preview"),
        (("enter",), "", "export"),
    ),
    (
        (("a", "s"), " or ", "adjust left bound"),
        (("<", ">"), " or ", "shift in-out"),
        (("left", "right"), " or ", "move cursor"),
        (("i", "["), "/", "mark in"),
        (("o", "]"), "/", "mark out"),
        (("d", "f"), " or ", "adjust right bound"),
    ),
    (
        (("(", ")"), " or ", "accept in or out suggestion"),
        (("w",), "", "toggle cursor wrap mode"),
        (("l",), "", "cycle loop type"),
    ),
)


def _format_loop_frame_counter(position: int, total_frames: int) -> str:
    width = max(2, len(str(max(0, total_frames))))
    return f"{position:0{width}d}/{total_frames}"


def _format_cursor_counter(position: int, max_position: int) -> str:
    width = max(2, len(str(max(0, max_position))))
    return f"{position:0{width}d}/{max_position:0{width}d}"


def _keycap_width(key: str, *, scale: float, thickness: int, pad_x: int, min_w: int) -> int:
    if key == "enter":
        return max(min_w + 16, 40)
    if key in {"left", "right"}:
        return max(min_w + 10, 32)
    tw, _ = text_wh(key, scale, thickness)
    return max(min_w, tw + pad_x * 2)


def _keycap_height(key: str, *, scale: float, thickness: int, pad_y: int) -> int:
    left, top, right, bottom = text_bbox(key, scale, thickness)
    return (bottom - top) + pad_y * 2


def _entry_width(
    entry: LegendEntry,
    *,
    key_scale: float,
    key_thickness: int,
    label_scale: float,
    label_thickness: int,
    key_pad_x: int,
    key_min_w: int,
) -> int:
    keys, joiner, label = entry
    width = sum(_keycap_width(key, scale=key_scale, thickness=key_thickness, pad_x=key_pad_x, min_w=key_min_w) for key in keys)
    if len(keys) > 1:
        joiner_w, _ = text_wh(joiner, label_scale, label_thickness)
        width += joiner_w * (len(keys) - 1)
    label_w, _ = text_wh(f": {label}", label_scale, label_thickness)
    return width + label_w


def _draw_keycap(
    canvas: np.ndarray,
    key: str,
    x: int,
    y: int,
    *,
    put_text: Callable[..., Any],
    scale: float,
    thickness: int,
    pad_x: int,
    pad_y: int,
    min_w: int,
) -> int:
    width = _keycap_width(key, scale=scale, thickness=thickness, pad_x=pad_x, min_w=min_w)
    left, top, right, bottom = text_bbox(key, scale, thickness)
    tw, th = right - left, bottom - top
    height = _keycap_height(key, scale=scale, thickness=thickness, pad_y=pad_y)
    cv2.rectangle(canvas, (x, y), (x + width, y + height), (72, 72, 72), -1)
    icon_color = (240, 240, 240)
    if key == "left":
        cy = y + height // 2
        cv2.line(canvas, (x + width - 9, cy), (x + 10, cy), icon_color, 1, cv2.LINE_AA)
        cv2.line(canvas, (x + 10, cy), (x + 16, cy - 5), icon_color, 1, cv2.LINE_AA)
        cv2.line(canvas, (x + 10, cy), (x + 16, cy + 5), icon_color, 1, cv2.LINE_AA)
    elif key == "right":
        cy = y + height // 2
        cv2.line(canvas, (x + 9, cy), (x + width - 10, cy), icon_color, 1, cv2.LINE_AA)
        cv2.line(canvas, (x + width - 10, cy), (x + width - 16, cy - 5), icon_color, 1, cv2.LINE_AA)
        cv2.line(canvas, (x + width - 10, cy), (x + width - 16, cy + 5), icon_color, 1, cv2.LINE_AA)
    elif key == "enter":
        top_y = y + max(5, (height - 14) // 2)
        mid_x = x + width - 11
        left_x = x + 10
        base_y = y + height - max(5, (height - 14) // 2)
        cv2.line(canvas, (mid_x, top_y), (mid_x, base_y - 4), icon_color, 1, cv2.LINE_AA)
        cv2.line(canvas, (mid_x, base_y - 4), (left_x, base_y - 4), icon_color, 1, cv2.LINE_AA)
        cv2.line(canvas, (left_x, base_y - 4), (left_x + 6, base_y - 9), icon_color, 1, cv2.LINE_AA)
        cv2.line(canvas, (left_x, base_y - 4), (left_x + 6, base_y + 1), icon_color, 1, cv2.LINE_AA)
    else:
        tx = x + (width - tw) // 2 - left
        ty = y + (height - th) // 2 - top
        put_text(canvas, key, tx, ty, scale, icon_color, thickness)
    return width


def _draw_hotkey_legend_row(
    canvas: np.ndarray,
    row: tuple[LegendEntry, ...],
    center_x: int,
    y: int,
    *,
    put_text: Callable[..., Any],
) -> None:
    key_scale = 0.44
    key_thickness = 1
    label_scale = 0.5
    label_thickness = 1
    key_pad_x = 8
    key_pad_y = 4
    key_min_w = 22
    item_gap = 18
    _, label_top, _, label_bottom = text_bbox("accept in or out suggestion", label_scale, label_thickness)
    label_height = label_bottom - label_top
    row_height = max(
        _keycap_height("space", scale=key_scale, thickness=key_thickness, pad_y=key_pad_y),
        label_height,
    )
    label_y = y + (row_height - label_height) // 2 - label_top

    row_width = 0
    for idx, entry in enumerate(row):
        row_width += _entry_width(
            entry,
            key_scale=key_scale,
            key_thickness=key_thickness,
            label_scale=label_scale,
            label_thickness=label_thickness,
            key_pad_x=key_pad_x,
            key_min_w=key_min_w,
        )
        if idx < len(row) - 1:
            row_width += item_gap

    x = center_x - row_width // 2
    for idx, (keys, joiner, label) in enumerate(row):
        for key_idx, key in enumerate(keys):
            x += _draw_keycap(
                canvas,
                key,
                x,
                y,
                put_text=put_text,
                scale=key_scale,
                thickness=key_thickness,
                pad_x=key_pad_x,
                pad_y=key_pad_y,
                min_w=key_min_w,
            )
            if key_idx < len(keys) - 1:
                put_text(canvas, joiner, x + 2, label_y, label_scale, (205, 205, 205), label_thickness)
                joiner_w, _ = text_wh(joiner, label_scale, label_thickness)
                x += joiner_w
        put_text(canvas, f": {label}", x + 2, label_y, label_scale, (225, 225, 225), label_thickness)
        label_w, _ = text_wh(f": {label}", label_scale, label_thickness)
        x += label_w
        if idx < len(row) - 1:
            x += item_gap


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
    legend_y1 = mode_y + 44
    legend_y2 = legend_y1 + 30
    legend_y3 = legend_y2 + 30

    put_text_centered(canvas, state.session_name, session_cx, session_y, 0.92, (240, 240, 240), 2)
    meta_text = f"file: {os.path.basename(state.path)}     fps: {state.fps:.3f}"
    put_text_centered(canvas, meta_text, session_cx, meta_y, 0.58, (230, 230, 230), 1)

    left_cx = left_x + pane_w // 2
    right_cx = right_x + pane_w // 2
    put_text_centered(canvas, "frame at cursor", left_cx, title_y, 0.9, (240, 240, 240), 2)
    put_text_centered(canvas, "loop preview", right_cx, title_y, 0.9, (240, 240, 240), 2)

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
    cursor_counter = _format_cursor_counter(rel_cur, state.loaded_count - 1)
    put_text(canvas, f"cursor: {cursor_counter} @ {format_seconds(state.current / state.fps)}", left_x, info1_y, 0.58, (235, 235, 235), 1)
    preview_sequence = loop_preview_indices(state)
    preview_pos = state.paused_loop_pos if state.paused_loop_pos is not None else (preview_sequence.index(loop_idx) if loop_idx in preview_sequence else 0)
    loop_counter = _format_loop_frame_counter(preview_pos, len(preview_sequence))
    put_text(canvas, f"loop frame: {loop_counter} @ {format_seconds(loop_idx / state.fps)}", right_x, info1_y, 0.58, (235, 235, 235), 1)
    playback_status = "paused" if state.loop_paused else "playing"
    put_text(canvas, f"speed: {state.speed:.2f}x ({playback_status})", right_x, info2_y, 0.58, (235, 235, 235), 1)

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
    draw_button(canvas, state.buttons["export"], "export")

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

    _draw_hotkey_legend_row(canvas, HOTKEY_LEGEND_ROWS[0], session_cx, legend_y1, put_text=put_text)
    _draw_hotkey_legend_row(canvas, HOTKEY_LEGEND_ROWS[1], session_cx, legend_y2, put_text=put_text)
    _draw_hotkey_legend_row(canvas, HOTKEY_LEGEND_ROWS[2], session_cx, legend_y3, put_text=put_text)
    if state.session_warning:
        put_text_centered(canvas, state.session_warning, session_cx, legend_y1 - 14, 0.52, (120, 200, 255), 1)

    if state.export_job and not state.export_job.dismissed:
        draw_export_overlay(canvas, state)
    if state.exit_prompt_visible:
        draw_exit_overlay(canvas, state)

    return canvas
