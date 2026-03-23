from __future__ import annotations

import os

import cv2
import numpy as np

from .exit_prompt import EXIT_PROMPT_BUTTON_NAMES, EXIT_PROMPT_CHOICES
from .loop_modes import LOOP_MODE_LABELS
from .navigation import timeline_x_for_index
from .playback import current_loop_frame_index, loop_preview_indices
from .state import (
    VideoState,
    safe_frame,
)
from .utils import format_seconds

Rect = tuple[int, int, int, int]
Color = tuple[int, int, int]


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
