from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox
except Exception:
    tk = None
    filedialog = None
    messagebox = None

from .export import start_export_job, terminate_export_subprocesses
from .loop_modes import LOOP_MODE_LABELS
from .paths import (
    ACCEPT_SUGGESTED_IN_KEYS,
    ACCEPT_SUGGESTED_OUT_KEYS,
    BOUNDS_CONTRACT_LEFT_KEYS,
    BOUNDS_CONTRACT_RIGHT_KEYS,
    BOUNDS_EXTEND_LEFT_KEYS,
    BOUNDS_EXTEND_RIGHT_KEYS,
    ENTER_KEYS,
    ESC_KEYS,
    LAST_SESSION_FILE,
    LOOP_MODE_CYCLE_KEYS,
    MARK_IN_KEYS,
    MARK_OUT_KEYS,
    PLAY_PAUSE_KEYS,
    MODULE_DIR,
    QUIT_KEYS,
    SHIFT_RANGE_LEFT_KEYS,
    SHIFT_RANGE_RIGHT_KEYS,
    SPEED_DOWN_KEYS,
    SPEED_UP_KEYS,
    TAB_KEYS,
    WIN_LEFT_KEYS,
    WIN_RIGHT_KEYS,
    WRAP_TOGGLE_KEYS,
)
from .state import (
    VideoState,
    accept_suggested_in,
    accept_suggested_out,
    change_speed,
    contract_left,
    cycle_loop_mode,
    contract_right,
    current_loop_frame_index,
    extend_left,
    extend_right,
    index_for_timeline_x,
    loop_preview_indices,
    move_current_left,
    move_current_right,
    restore_original_session,
    safe_frame,
    set_mark_in,
    set_mark_out,
    shift_active_range,
    timeline_x_for_index,
    toggle_loop_pause,
    toggle_wrap_mode,
)
from .utils import format_seconds, parse_timestamp, sanitize_name
from .vlc_prefill import detect_vlc_session_prefill

Rect = tuple[int, int, int, int]
Color = tuple[int, int, int]
APP_DISPLAY_NAME = "Clipper"
EXIT_PROMPT_CHOICES = ("save", "discard", "cancel")
EXIT_PROMPT_BUTTON_NAMES = {
    "save": "exit_save",
    "discard": "exit_discard",
    "cancel": "exit_cancel",
}


def _clipper_icon_path() -> Path:
    return MODULE_DIR / "clipper.ico"


def _set_tk_window_icon(root: Any) -> None:
    icon_path = _clipper_icon_path()
    if not icon_path.exists():
        return
    try:
        root.iconbitmap(str(icon_path))
    except Exception:
        pass


def _set_cv2_window_icon(window_name: str) -> None:
    if sys.platform != "win32":
        return
    icon_path = _clipper_icon_path()
    if not icon_path.exists():
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        image_icon = 1
        load_from_file = 0x10
        wm_seticon = 0x80
        icon_small = 0
        icon_big = 1
        hwnd = user32.FindWindowW(None, window_name)
        if not hwnd:
            return
        hicon = user32.LoadImageW(None, str(icon_path), image_icon, 0, 0, load_from_file)
        if not hicon:
            return
        user32.SendMessageW(hwnd, wm_seticon, icon_small, hicon)
        user32.SendMessageW(hwnd, wm_seticon, icon_big, hicon)
    except Exception:
        pass


def scale_to_fit(img: np.ndarray, max_w: int, max_h: int) -> np.ndarray:
    h, w = img.shape[:2]
    if h <= max_h and w <= max_w:
        return img
    scale = min(max_w / w, max_h / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


def put_text(
    img: np.ndarray,
    text: str,
    x: int,
    y: int,
    scale: float = 0.6,
    color: Color = (230, 230, 230),
    thickness: int = 1,
) -> None:
    cv2.putText(img, text, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def text_wh(text: str, scale: float = 0.6, thickness: int = 1) -> tuple[int, int]:
    size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)[0]
    return int(size[0]), int(size[1])


def put_text_centered(
    img: np.ndarray,
    text: str,
    center_x: int,
    y: int,
    scale: float = 0.6,
    color: Color = (230, 230, 230),
    thickness: int = 1,
) -> None:
    tw, _ = text_wh(text, scale, thickness)
    put_text(img, text, center_x - tw // 2, y, scale, color, thickness)


def draw_button(
    img: np.ndarray,
    rect: Rect,
    text: str,
    enabled: bool = True,
    active: bool = False,
    fill_color: Color | None = None,
    active_fill_color: Color | None = None,
    icon: str | None = None,
    focused: bool = False,
    focus_border_color: Color = (110, 220, 255),
    focus_border_thickness: int = 3,
) -> None:
    x1, y1, x2, y2 = map(int, rect)
    fill = fill_color if fill_color is not None else ((62, 62, 62) if enabled else (40, 40, 40))
    if active and enabled:
        fill = active_fill_color if active_fill_color is not None else (80, 90, 130)
    if not enabled and fill_color is not None:
        fill = tuple(max(20, int(c * 0.55)) for c in fill_color)
    border = (210, 210, 210) if enabled else (95, 95, 95)
    color = (240, 240, 240) if enabled else (120, 120, 120)
    cv2.rectangle(img, (x1, y1), (x2, y2), fill, -1)
    cv2.rectangle(img, (x1, y1), (x2, y2), border, 1)
    if focused and enabled:
        cv2.rectangle(img, (x1 - 2, y1 - 2), (x2 + 2, y2 + 2), focus_border_color, focus_border_thickness)
    if icon == "play":
        width = x2 - x1
        height = y2 - y1
        tri_h = max(12, int(round(height * 0.5)))
        tri_w = max(10, int(round(tri_h * 0.7)))
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        left_x = cx - tri_w // 2
        top_y = cy - tri_h // 2
        bottom_y = cy + tri_h // 2
        pts = np.array(
            [
                (left_x, top_y),
                (left_x, bottom_y),
                (left_x + tri_w, cy),
            ],
            dtype=np.int32,
        )
        cv2.fillConvexPoly(img, pts, color)
        return
    if icon == "pause":
        bar_w = max(5, (x2 - x1) // 8)
        bar_h_margin = max(6, (y2 - y1) // 5)
        gap = max(6, bar_w)
        center_x = (x1 + x2) // 2
        left_x1 = center_x - gap // 2 - bar_w
        right_x1 = center_x + gap // 2
        cv2.rectangle(img, (left_x1, y1 + bar_h_margin), (left_x1 + bar_w, y2 - bar_h_margin), color, -1)
        cv2.rectangle(img, (right_x1, y1 + bar_h_margin), (right_x1 + bar_w, y2 - bar_h_margin), color, -1)
        return
    ts = 0.7
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, ts, 2)
    tx = x1 + max(0, (x2 - x1 - tw) // 2)
    ty = y1 + max(th + 2, (y2 - y1 + th) // 2)
    put_text(img, text, tx, ty, ts, color, 2)


def point_in_rect(x: int, y: int, rect: Rect) -> bool:
    x1, y1, x2, y2 = rect
    return x1 <= x <= x2 and y1 <= y <= y2


def draw_dotted_vertical_line(
    img: np.ndarray,
    x: int,
    y1: int,
    y2: int,
    color: Color,
    *,
    segment: int = 6,
    gap: int = 4,
    thickness: int = 1,
) -> None:
    y = y1
    while y <= y2:
        seg_end = min(y2, y + segment)
        cv2.line(img, (x, y), (x, seg_end), color, thickness)
        y = seg_end + gap


def build_ui(state: VideoState) -> np.ndarray:
    state.buttons = {}
    current_frame = safe_frame(state, state.current)
    loop_idx = current_loop_frame_index(state)
    loop_frame = safe_frame(state, loop_idx)

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


def draw_progress_bar(
    img: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
    p: float,
    color: Color = (110, 210, 110),
    label: str = "",
) -> None:
    cv2.rectangle(img, (x, y), (x + w, y + h), (215, 215, 215), 1)
    fill = max(0, min(w, int(round(w * max(0.0, min(1.0, p))))))
    if fill > 0:
        cv2.rectangle(img, (x + 1, y + 1), (x + fill - 1, y + h - 1), color, -1)
    if label:
        tw, th = text_wh(label, 0.5, 1)
        tx = x + (w - tw) // 2
        ty = y + (h + th) // 2
        put_text(img, label, tx, ty, 0.5, (240, 240, 240), 1)


def draw_export_overlay(canvas: np.ndarray, state: VideoState) -> None:
    job = state.export_job
    if not job:
        return
    h, w = canvas.shape[:2]
    ow, oh = w - 180, 630
    ox, oy = 90, (h - oh) // 2
    shade = canvas.copy()
    cv2.rectangle(shade, (0, 0), (w, h), (0, 0, 0), -1)
    canvas[:] = cv2.addWeighted(canvas, 0.35, shade, 0.65, 0)
    cv2.rectangle(canvas, (ox, oy), (ox + ow, oy + oh), (50, 50, 50), -1)
    cv2.rectangle(canvas, (ox, oy), (ox + ow, oy + oh), (215, 215, 215), 1)
    state.buttons["overlay_close"] = (ox + 10, oy + 10, ox + 42, oy + 42)
    draw_button(canvas, state.buttons["overlay_close"], "X")
    title = "Export complete" if job.done and not job.failed else "Export failed" if job.failed else "Exporting"
    title_color = (120, 240, 120) if job.done and not job.failed else (60, 60, 255) if job.failed else (240, 240, 240)
    put_text(canvas, title, ox + 60, oy + 44, 1.2, title_color, 2)

    sections = [
        ("1. Raw MP4 export", job.clip_status, job.clip_progress),
        ("2. Normalize loop shape, smooth seam, and resize if needed", job.fix_status, job.fix_progress),
        ("3. Extract audio", job.audio_status, job.audio_progress),
    ]
    y = oy + 140
    for label, status, prog in sections:
        put_text(canvas, label, ox + 26, y, 0.9, (240, 240, 240), 2)
        put_text(canvas, status, ox + 26, y + 34, 0.65)
        bar_label = f"{int(round(prog * 100))}%" if 0.0 < prog < 1.0 else ""
        draw_progress_bar(canvas, ox + 26, y + 52, ow - 52, 28, prog, label=bar_label)
        y += 120

    foot_y = oy + oh - 86
    if job.failed:
        for i, part in enumerate(job.error_message.splitlines()[:3]):
            put_text(canvas, part[:120], ox + 26, foot_y + i * 24, 0.58, (90, 90, 255))
    else:
        if job.clip_output:
            put_text(canvas, f"Final clip: {job.clip_output}", ox + 26, foot_y, 0.54)
        if job.audio_output:
            put_text(canvas, f"Audio MP3: {job.audio_output}", ox + 26, foot_y + 24, 0.54)
        if job.done:
            put_text(canvas, "Esc or X closes this overlay. You can keep working in the main screen.", ox + 26, foot_y + 52, 0.62, (120, 240, 120))


def draw_exit_overlay(canvas: np.ndarray, state: VideoState) -> None:
    h, w = canvas.shape[:2]
    ox, oy = 340, 320
    ow, oh = w - 680, 220
    shade = canvas.copy()
    cv2.rectangle(shade, (0, 0), (w, h), (0, 0, 0), -1)
    canvas[:] = cv2.addWeighted(canvas, 0.35, shade, 0.65, 0)
    cv2.rectangle(canvas, (ox, oy), (ox + ow, oy + oh), (50, 50, 50), -1)
    cv2.rectangle(canvas, (ox, oy), (ox + ow, oy + oh), (215, 215, 215), 1)
    put_text(canvas, "Changes detected.", ox + 28, oy + 42, 0.95, (240, 240, 240), 2)
    put_text(canvas, "Choose how to exit this session.", ox + 28, oy + 82, 0.62, (230, 230, 230), 1)

    by = oy + oh - 70
    bw = 190
    bh = 38
    gap = 14
    total = bw * 3 + gap * 2
    bx = ox + (ow - total) // 2
    state.buttons[EXIT_PROMPT_BUTTON_NAMES["save"]] = (bx, by, bx + bw, by + bh)
    state.buttons[EXIT_PROMPT_BUTTON_NAMES["discard"]] = (bx + bw + gap, by, bx + 2 * bw + gap, by + bh)
    state.buttons[EXIT_PROMPT_BUTTON_NAMES["cancel"]] = (bx + 2 * (bw + gap), by, bx + 3 * bw + 2 * gap, by + bh)
    focus = state.exit_prompt_focus if state.exit_prompt_focus in EXIT_PROMPT_CHOICES else "save"
    draw_button(canvas, state.buttons[EXIT_PROMPT_BUTTON_NAMES["save"]], "Save and exit", focused=focus == "save")
    draw_button(canvas, state.buttons[EXIT_PROMPT_BUTTON_NAMES["discard"]], "Exit w/o save", focused=focus == "discard")
    draw_button(canvas, state.buttons[EXIT_PROMPT_BUTTON_NAMES["cancel"]], "Cancel exit", focused=focus == "cancel")
    put_text(canvas, "Tab: Change selection    Enter: Confirm    Esc: Cancel exit", ox + 28, by - 18, 0.56, (215, 215, 215), 1)


def cycle_exit_prompt_focus(state: VideoState) -> None:
    current = state.exit_prompt_focus if state.exit_prompt_focus in EXIT_PROMPT_CHOICES else "save"
    next_index = (EXIT_PROMPT_CHOICES.index(current) + 1) % len(EXIT_PROMPT_CHOICES)
    state.exit_prompt_focus = EXIT_PROMPT_CHOICES[next_index]
    state.render_rev += 1


def queue_exit_prompt_action(state: VideoState, choice: str | None = None) -> None:
    selected = choice if choice in EXIT_PROMPT_CHOICES else state.exit_prompt_focus
    if selected not in EXIT_PROMPT_CHOICES:
        selected = "save"
    state.exit_prompt_focus = selected
    state.exit_prompt_action = selected
    state.render_rev += 1


def show_exit_prompt(state: VideoState) -> None:
    if state.exit_prompt_focus not in EXIT_PROMPT_CHOICES:
        state.exit_prompt_focus = "save"
    state.exit_prompt_visible = True
    state.exit_prompt_action = ""
    state.render_rev += 1


def on_mouse(event: int, x: int, y: int, flags: int, userdata: Any | None) -> None:
    if not isinstance(userdata, VideoState):
        return
    state = userdata
    state.mouse_x = x
    state.mouse_y = y
    if event == cv2.EVENT_LBUTTONDOWN:
        if state.exit_prompt_visible:
            for choice in EXIT_PROMPT_CHOICES:
                rect = state.buttons.get(EXIT_PROMPT_BUTTON_NAMES[choice])
                if rect and point_in_rect(x, y, rect):
                    queue_exit_prompt_action(state, choice)
                    return
            return
        for name, rect in list(state.buttons.items()):
            if point_in_rect(x, y, rect):
                if name == "speed_down":
                    change_speed(state, -0.25)
                elif name == "speed_up":
                    change_speed(state, +0.25)
                elif name == "play_pause":
                    toggle_loop_pause(state)
                elif name == "export":
                    start_export_job(state)
                elif name == "extend_left" and state.loaded_start > 0:
                    extend_left(state)
                elif name == "contract_left" and (state.active_start - state.loaded_start) >= state.base_step:
                    contract_left(state)
                elif name == "contract_right" and (state.loaded_end - state.active_end) >= state.base_step:
                    contract_right(state)
                elif name == "extend_right" and state.loaded_end < state.total_frames - 1:
                    extend_right(state)
                elif name == "shift_left":
                    shift_active_range(state, -1)
                elif name == "shift_right":
                    shift_active_range(state, 1)
                elif name == "mark_in" and state.current < state.active_end:
                    set_mark_in(state)
                elif name == "mark_out" and state.current > state.active_start:
                    set_mark_out(state)
                elif name == "wrap":
                    toggle_wrap_mode(state)
                elif name == "loop_mode":
                    cycle_loop_mode(state)
                elif name == "overlay_close" and state.export_job:
                    state.export_job.dismissed = True
                elif name == "timeline":
                    state.current = index_for_timeline_x(state, rect[0], rect[2], x)
                    state.render_rev += 1
                break
        else:
            tl = state.buttons.get("timeline")
            if tl and point_in_rect(x, y, tl):
                state.current = index_for_timeline_x(state, tl[0], tl[2], x)
                state.render_rev += 1


def launcher_dialog() -> dict[str, Any]:
    if tk is None or filedialog is None or messagebox is None:
        raise RuntimeError("tkinter is required for the launcher on this system")
    dialog = cast(Any, filedialog)
    msgbox = cast(Any, messagebox)
    root = tk.Tk()
    _set_tk_window_icon(root)
    root.title(f"{APP_DISPLAY_NAME} Launcher")
    root.geometry("1040x560")
    root.resizable(False, False)

    vlc_prefill = detect_vlc_session_prefill()
    mode = tk.StringVar(value="new" if vlc_prefill or not LAST_SESSION_FILE.exists() else "load")
    last_session = LAST_SESSION_FILE.read_text(encoding="utf-8").strip() if LAST_SESSION_FILE.exists() else ""
    session_json = tk.StringVar(value=last_session)
    session_name = tk.StringVar(value=vlc_prefill.session_name if vlc_prefill else "")
    video_file = tk.StringVar(value=vlc_prefill.video_file if vlc_prefill else "")
    timestamp = tk.StringVar(value=vlc_prefill.timestamp if vlc_prefill else "00:00:00")
    seconds = tk.StringVar(value="5")
    loop_mode = tk.StringVar(value="base-tip-base")
    prefill_note = tk.StringVar(
        value=vlc_prefill.note if vlc_prefill else "If VLC is open, Clippeer will try to prefill this section."
    )
    result: dict[str, Any] = {"ok": False}

    def browse_json() -> None:
        p = dialog.askopenfilename(filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if p:
            session_json.set(p)
            mode.set("load")

    def browse_video() -> None:
        p = dialog.askopenfilename(filetypes=[("Video files", "*.mp4 *.mkv *.mov *.avi *.webm"), ("All files", "*.*")])
        if p:
            video_file.set(p)
            mode.set("new")

    def open_it(event: Any = None) -> None:
        try:
            if mode.get() == "load":
                p = Path(session_json.get().strip())
                if not p.is_file():
                    raise ValueError("Choose a valid session JSON")
                result.update({"ok": True, "mode": "load", "session_json": str(p)})
            else:
                name = sanitize_name(session_name.get())
                if not name:
                    raise ValueError("Enter a session name")
                vf = Path(video_file.get().strip())
                if not vf.is_file():
                    raise ValueError("Choose a valid video file")
                sec = float(seconds.get())
                if sec <= 0:
                    raise ValueError("Seconds must be > 0")
                parse_timestamp(timestamp.get())
                result.update(
                    {
                        "ok": True,
                        "mode": "new",
                        "session_name": name,
                        "video_file": str(vf),
                        "timestamp": timestamp.get().strip(),
                        "seconds": sec,
                        "loop_mode": loop_mode.get(),
                    }
                )
            root.destroy()
        except Exception as exc:
            msgbox.showerror(APP_DISPLAY_NAME, f"ERROR: {exc}")

    def cancel(event: Any = None) -> None:
        root.destroy()

    padx = 16
    tk.Label(root, text="Open an existing session or start a new one.", font=("Segoe UI", 14)).pack(anchor="w", padx=padx, pady=(18, 10))

    frame1 = tk.Frame(root)
    frame1.pack(fill="x", padx=padx)
    tk.Radiobutton(frame1, text="Load previous session JSON", variable=mode, value="load", font=("Segoe UI", 11)).grid(row=0, column=0, sticky="w", pady=(0, 8))
    tk.Entry(frame1, textvariable=session_json, width=84).grid(row=1, column=0, sticky="we", padx=(28, 8), pady=(0, 8))
    tk.Button(frame1, text="Browse...", command=browse_json, width=12).grid(row=1, column=1, sticky="e", pady=(0, 8))
    frame1.grid_columnconfigure(0, weight=1)

    sep = tk.Frame(root, height=1, bg="#bbbbbb")
    sep.pack(fill="x", padx=padx, pady=8)

    frame2 = tk.Frame(root)
    frame2.pack(fill="x", padx=padx)
    tk.Radiobutton(frame2, text="Create new session", variable=mode, value="new", font=("Segoe UI", 11)).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
    tk.Label(frame2, text="Session name", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", padx=(28, 8), pady=6)
    tk.Entry(frame2, textvariable=session_name, width=56).grid(row=1, column=1, columnspan=2, sticky="w", pady=6)
    tk.Label(frame2, text="Video file", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", padx=(28, 8), pady=6)
    tk.Entry(frame2, textvariable=video_file, width=84).grid(row=2, column=1, sticky="we", pady=6, padx=(0, 8))
    tk.Button(frame2, text="Browse...", command=browse_video, width=12).grid(row=2, column=2, sticky="e", pady=6)
    tk.Label(frame2, text="Timestamp (hh:mm:ss)", font=("Segoe UI", 10)).grid(row=3, column=0, sticky="w", padx=(28, 8), pady=6)
    tk.Entry(frame2, textvariable=timestamp, width=20).grid(row=3, column=1, sticky="w", pady=6)
    tk.Label(frame2, text="Seconds", font=("Segoe UI", 10)).grid(row=4, column=0, sticky="w", padx=(28, 8), pady=6)
    tk.Entry(frame2, textvariable=seconds, width=10).grid(row=4, column=1, sticky="w", pady=6)
    tk.Label(frame2, text="Loop mode", font=("Segoe UI", 10)).grid(row=5, column=0, sticky="w", padx=(28, 8), pady=6)
    tk.OptionMenu(frame2, loop_mode, *LOOP_MODE_LABELS.keys()).grid(row=5, column=1, sticky="w", pady=6)
    tk.Label(frame2, textvariable=prefill_note, font=("Segoe UI", 9), fg="#4a6580", anchor="w").grid(
        row=6, column=0, columnspan=3, sticky="w", padx=(28, 0), pady=(8, 0)
    )
    frame2.grid_columnconfigure(1, weight=1)

    session_json.trace_add("write", lambda *_: mode.set("load"))
    session_name.trace_add("write", lambda *_: mode.set("new"))
    video_file.trace_add("write", lambda *_: mode.set("new"))
    timestamp.trace_add("write", lambda *_: mode.set("new"))
    seconds.trace_add("write", lambda *_: mode.set("new"))

    bottom = tk.Frame(root)
    bottom.pack(side="bottom", fill="x", padx=padx, pady=18)
    open_btn = tk.Button(bottom, text="Open", command=open_it, width=12, default="active")
    open_btn.pack(side="right", padx=(8, 0))
    tk.Button(bottom, text="Cancel", command=cancel, width=12).pack(side="right")

    root.bind("<Return>", open_it)
    root.bind("<Escape>", cancel)
    open_btn.focus_set()
    root.mainloop()
    return result


def _window_closed(window_name: str) -> bool:
    try:
        return cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


def handle_key(state: VideoState, key: int) -> None:
    if key in WIN_LEFT_KEYS:
        move_current_left(state)
    elif key in WIN_RIGHT_KEYS:
        move_current_right(state)
    elif key in BOUNDS_EXTEND_LEFT_KEYS:
        extend_left(state)
    elif key in BOUNDS_CONTRACT_LEFT_KEYS:
        contract_left(state)
    elif key in BOUNDS_CONTRACT_RIGHT_KEYS:
        contract_right(state)
    elif key in BOUNDS_EXTEND_RIGHT_KEYS:
        extend_right(state)
    elif key in MARK_IN_KEYS:
        set_mark_in(state)
    elif key in MARK_OUT_KEYS:
        set_mark_out(state)
    elif key in ACCEPT_SUGGESTED_IN_KEYS:
        accept_suggested_in(state)
    elif key in ACCEPT_SUGGESTED_OUT_KEYS:
        accept_suggested_out(state)
    elif key in SHIFT_RANGE_LEFT_KEYS:
        shift_active_range(state, -1)
    elif key in SHIFT_RANGE_RIGHT_KEYS:
        shift_active_range(state, 1)
    elif key in WRAP_TOGGLE_KEYS:
        toggle_wrap_mode(state)
    elif key in LOOP_MODE_CYCLE_KEYS:
        cycle_loop_mode(state)
    elif key in PLAY_PAUSE_KEYS:
        toggle_loop_pause(state)
    elif key in SPEED_DOWN_KEYS:
        change_speed(state, -0.25)
    elif key in SPEED_UP_KEYS:
        change_speed(state, 0.25)
    elif key in ENTER_KEYS:
        start_export_job(state)
    elif key in ESC_KEYS and state.export_job and not state.export_job.dismissed:
        state.export_job.dismissed = True


def run_ui(state: VideoState) -> None:
    window_name = APP_DISPLAY_NAME

    def ensure_window() -> None:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 1520, 960)
        _set_cv2_window_icon(window_name)
        cv2.setMouseCallback(window_name, on_mouse, state)

    def finish_exit(choice: str) -> bool:
        if choice == "cancel":
            state.exit_prompt_visible = False
            state.exit_prompt_focus = "save"
            state.exit_prompt_action = ""
            state.render_rev += 1
            return False
        if choice == "discard":
            restore_original_session(state)
        else:
            state.autosave_session()
        state.exit_prompt_visible = False
        state.exit_prompt_focus = "save"
        state.exit_prompt_action = ""
        return True

    def try_exit() -> bool:
        if not state.dirty:
            return True
        if not state.should_prompt_on_exit:
            state.autosave_session()
            return True
        show_exit_prompt(state)
        return False

    ensure_window()
    last_loop_idx = -1
    last_present = 0.0

    try:
        while True:
            if state.exit_prompt_action:
                if finish_exit(state.exit_prompt_action):
                    break
                continue

            loop_idx = current_loop_frame_index(state)
            now = time.monotonic()
            need_redraw = state.render_rev > 0 or state.exit_prompt_visible or (loop_idx != last_loop_idx and (now - last_present) >= (1.0 / 30.0))
            if need_redraw:
                last_loop_idx = loop_idx
                last_present = now
                state.render_rev = 0
                ui = build_ui(state)
                cv2.imshow(window_name, ui)

            if _window_closed(window_name):
                if state.exit_prompt_visible:
                    ensure_window()
                    state.render_rev += 1
                    continue
                if try_exit():
                    break
                ensure_window()
                continue

            key = cv2.waitKeyEx(20)

            if _window_closed(window_name):
                if state.exit_prompt_visible:
                    ensure_window()
                    state.render_rev += 1
                    continue
                if try_exit():
                    break
                ensure_window()
                continue

            if key == -1:
                continue

            if state.exit_prompt_visible:
                if key in TAB_KEYS:
                    cycle_exit_prompt_focus(state)
                elif key in ENTER_KEYS:
                    queue_exit_prompt_action(state)
                elif key in ESC_KEYS:
                    queue_exit_prompt_action(state, "cancel")
                elif key in QUIT_KEYS:
                    queue_exit_prompt_action(state, "cancel")
                continue

            if key in ESC_KEYS:
                if state.export_job and not state.export_job.dismissed:
                    state.export_job.dismissed = True
                    state.render_rev += 1
                    continue
                if try_exit():
                    break
                continue

            if key in QUIT_KEYS:
                if try_exit():
                    break
                continue

            handle_key(state, key)
    finally:
        terminate_export_subprocesses(state)
        state.cap.release()
        try:
            cv2.setMouseCallback(window_name, lambda *args: None)
        except Exception:
            pass
        try:
            cv2.destroyWindow(window_name)
        except cv2.error:
            pass
        try:
            cv2.destroyAllWindows()
        except cv2.error:
            pass
        for _ in range(6):
            try:
                cv2.waitKey(1)
            except cv2.error:
                break
            time.sleep(0.01)
