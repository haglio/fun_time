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
from .paths import (
    ENTER_KEYS,
    ESC_KEYS,
    LAST_SESSION_FILE,
    LOOP_FIX_SCRIPT,
    MARK_IN_KEYS,
    MARK_OUT_KEYS,
    MODULE_DIR,
    QUIT_KEYS,
    SPEED_DOWN_KEYS,
    SPEED_UP_KEYS,
    WIN_LEFT_KEYS,
    WIN_RIGHT_KEYS,
    WRAP_TOGGLE_KEYS,
)
from .state import (
    VideoState,
    change_speed,
    contract_left,
    contract_right,
    current_loop_frame_index,
    extend_left,
    extend_right,
    index_for_timeline_x,
    move_current_left,
    move_current_right,
    restore_original_session,
    safe_frame,
    set_mark_in,
    set_mark_out,
    timeline_x_for_index,
    toggle_wrap_mode,
)
from .utils import format_seconds, parse_timestamp, sanitize_name

Rect = tuple[int, int, int, int]
Color = tuple[int, int, int]


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
) -> None:
    x1, y1, x2, y2 = map(int, rect)
    fill = fill_color if fill_color is not None else ((62, 62, 62) if enabled else (40, 40, 40))
    if active and enabled:
        fill = active_fill_color if active_fill_color is not None else (80, 90, 130)
    if not enabled and fill_color is not None:
        fill = tuple(max(20, int(c * 0.55)) for c in fill_color)
    border = (210, 210, 210) if enabled else (95, 95, 95)
    cv2.rectangle(img, (x1, y1), (x2, y2), fill, -1)
    cv2.rectangle(img, (x1, y1), (x2, y2), border, 1)
    ts = 0.7
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, ts, 2)
    tx = x1 + max(0, (x2 - x1 - tw) // 2)
    ty = y1 + max(th + 2, (y2 - y1 + th) // 2)
    color = (240, 240, 240) if enabled else (120, 120, 120)
    put_text(img, text, tx, ty, ts, color, 2)


def point_in_rect(x: int, y: int, rect: Rect) -> bool:
    x1, y1, x2, y2 = rect
    return x1 <= x <= x2 and y1 <= y <= y2


def build_ui(state: VideoState) -> np.ndarray:
    state.buttons = {}
    current_frame = safe_frame(state, state.current)
    loop_idx = current_loop_frame_index(state)
    loop_frame = safe_frame(state, loop_idx)

    pane_w = 720
    pane_h = 500
    canvas_w = 1520
    canvas_h = 1000
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
    timeline_y = 796
    timeline_h = 22
    wrap_y = timeline_y + 54
    range_info_y = wrap_y + 42
    legend_y = 960

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
    put_text(canvas, f"Loop frame: {loop_idx - state.loaded_start} @ {format_seconds(loop_idx / state.fps)}", right_x, info1_y, 0.58, (235, 235, 235), 1)
    put_text(canvas, f"Speed: {state.speed:.2f}x", right_x, info2_y, 0.58, (235, 235, 235), 1)

    b_h = 34
    speed_w = 60
    export_w = 120
    gap = 8
    yb = info1_y - 22
    bx3 = right_x + pane_w - export_w
    bx2 = bx3 - 12 - speed_w
    bx1 = bx2 - gap - speed_w
    state.buttons["speed_down"] = (bx1, yb, bx1 + speed_w, yb + b_h)
    state.buttons["speed_up"] = (bx2, yb, bx2 + speed_w, yb + b_h)
    state.buttons["export"] = (bx3, yb, bx3 + export_w, yb + b_h)
    draw_button(canvas, state.buttons["speed_down"], "-")
    draw_button(canvas, state.buttons["speed_up"], "+")
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
    cv2.rectangle(canvas, (cur_x - 1, timeline_y - 4), (cur_x + 1, timeline_y + timeline_h + 4), (255, 255, 255), -1)
    cv2.rectangle(canvas, (loop_x_t - 1, timeline_y - 4), (loop_x_t + 1, timeline_y + timeline_h + 4), (50, 50, 255), -1)
    cv2.rectangle(canvas, (tl_x1, timeline_y), (tl_x2, timeline_y + timeline_h), (220, 220, 220), 1)
    state.buttons["timeline"] = (tl_x1, timeline_y - 8, tl_x2, timeline_y + timeline_h + 8)

    enable_in = state.current < state.active_end
    enable_out = state.current > state.active_start
    cursor_x = cur_x
    mark_y = timeline_y - 48
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

    rel_in = state.active_start - state.loaded_start
    rel_out = state.active_end - state.loaded_start
    wrap_label = "Wrap: Loaded" if state.wrap_mode == "blue" else "Wrap: In-Out"
    range_text = f"In-Out: {rel_in}-{rel_out}     Loaded: 0-{state.loaded_count - 1}     {wrap_label}"
    put_text_centered(canvas, range_text, session_cx, range_info_y, 0.58, (230, 230, 230), 1)

    legend = "Left/Right: Move cursor   i or [: Mark In   o or ]: Mark Out   m: Toggle wrap mode   -/+: Adjust speed   Enter: Export"
    put_text_centered(canvas, legend, session_cx, legend_y, 0.56, (230, 230, 230), 1)
    if state.session_warning:
        put_text_centered(canvas, state.session_warning, session_cx, legend_y - 26, 0.52, (120, 200, 255), 1)

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
) -> None:
    cv2.rectangle(img, (x, y), (x + w, y + h), (215, 215, 215), 1)
    fill = max(0, min(w, int(round(w * max(0.0, min(1.0, p))))))
    if fill > 0:
        cv2.rectangle(img, (x + 1, y + 1), (x + fill - 1, y + h - 1), color, -1)


def draw_export_overlay(canvas: np.ndarray, state: VideoState) -> None:
    job = state.export_job
    if not job:
        return
    h, w = canvas.shape[:2]
    ox, oy = 90, 70
    ow, oh = w - 180, h - 140
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
    put_text(canvas, f"Stage: {job.stage}", ox + 26, oy + 88, 0.7)

    sections = [
        ("1. Raw MP4 export", job.clip_status, job.clip_progress),
        (f"2. {LOOP_FIX_SCRIPT.name}", job.fix_status, job.fix_progress),
        ("3. Full-audio MP3 export", job.audio_status, job.audio_progress),
    ]
    y = oy + 140
    for label, status, prog in sections:
        put_text(canvas, label, ox + 26, y, 0.9, (240, 240, 240), 2)
        put_text(canvas, status, ox + 26, y + 34, 0.65)
        draw_progress_bar(canvas, ox + 26, y + 52, ow - 52, 28, prog)
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
    state.buttons["exit_save"] = (bx, by, bx + bw, by + bh)
    state.buttons["exit_discard"] = (bx + bw + gap, by, bx + 2 * bw + gap, by + bh)
    state.buttons["exit_cancel"] = (bx + 2 * (bw + gap), by, bx + 3 * bw + 2 * gap, by + bh)
    draw_button(canvas, state.buttons["exit_save"], "Save and exit", active=True)
    draw_button(canvas, state.buttons["exit_discard"], "Exit w/o save")
    draw_button(canvas, state.buttons["exit_cancel"], "Cancel exit")
    put_text(canvas, "Enter: Save and exit    Esc: Cancel exit", ox + 28, by - 18, 0.56, (215, 215, 215), 1)


def on_mouse(event: int, x: int, y: int, flags: int, userdata: Any | None) -> None:
    if not isinstance(userdata, VideoState):
        return
    state = userdata
    state.mouse_x = x
    state.mouse_y = y
    if event == cv2.EVENT_LBUTTONDOWN:
        if state.exit_prompt_visible:
            for name in ("exit_save", "exit_discard", "exit_cancel"):
                rect = state.buttons.get(name)
                if rect and point_in_rect(x, y, rect):
                    state.exit_prompt_action = {
                        "exit_save": "save",
                        "exit_discard": "discard",
                        "exit_cancel": "cancel",
                    }[name]
                    state.render_rev += 1
                    return
            return
        for name, rect in list(state.buttons.items()):
            if point_in_rect(x, y, rect):
                if name == "speed_down":
                    change_speed(state, -0.25)
                elif name == "speed_up":
                    change_speed(state, +0.25)
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
                elif name == "mark_in" and state.current < state.active_end:
                    set_mark_in(state)
                elif name == "mark_out" and state.current > state.active_start:
                    set_mark_out(state)
                elif name == "wrap":
                    toggle_wrap_mode(state)
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
    root.title("Frame Loop Trimmer Launcher")
    root.geometry("1040x560")
    root.resizable(False, False)

    mode = tk.StringVar(value="load" if LAST_SESSION_FILE.exists() else "new")
    last_session = LAST_SESSION_FILE.read_text(encoding="utf-8").strip() if LAST_SESSION_FILE.exists() else ""
    session_json = tk.StringVar(value=last_session)
    session_name = tk.StringVar(value="")
    video_file = tk.StringVar(value="")
    timestamp = tk.StringVar(value="00:00:00")
    seconds = tk.StringVar(value="5")
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
                    }
                )
            root.destroy()
        except Exception as exc:
            msgbox.showerror("Frame Loop Trimmer", f"ERROR: {exc}")

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
    elif key in MARK_IN_KEYS:
        set_mark_in(state)
    elif key in MARK_OUT_KEYS:
        set_mark_out(state)
    elif key in WRAP_TOGGLE_KEYS:
        toggle_wrap_mode(state)
    elif key in SPEED_DOWN_KEYS:
        change_speed(state, -0.25)
    elif key in SPEED_UP_KEYS:
        change_speed(state, 0.25)
    elif key in ENTER_KEYS:
        start_export_job(state)
    elif key in ESC_KEYS and state.export_job and not state.export_job.dismissed:
        state.export_job.dismissed = True


def run_ui(state: VideoState) -> None:
    window_name = "Frame Loop Trimmer"

    def ensure_window() -> None:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 1520, 960)
        _set_cv2_window_icon(window_name)
        cv2.setMouseCallback(window_name, on_mouse, state)

    def finish_exit(choice: str) -> bool:
        if choice == "cancel":
            state.exit_prompt_visible = False
            state.exit_prompt_action = ""
            state.render_rev += 1
            return False
        if choice == "discard":
            restore_original_session(state)
        else:
            state.autosave_session()
        state.exit_prompt_visible = False
        state.exit_prompt_action = ""
        return True

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
                if not state.dirty:
                    break
                state.exit_prompt_visible = True
                state.exit_prompt_action = ""
                ensure_window()
                state.render_rev += 1
                continue

            key = cv2.waitKeyEx(20)

            if _window_closed(window_name):
                if state.exit_prompt_visible:
                    ensure_window()
                    state.render_rev += 1
                    continue
                if not state.dirty:
                    break
                state.exit_prompt_visible = True
                state.exit_prompt_action = ""
                ensure_window()
                state.render_rev += 1
                continue

            if key == -1:
                continue

            if state.exit_prompt_visible:
                if key in ENTER_KEYS:
                    state.exit_prompt_action = "save"
                elif key in ESC_KEYS:
                    state.exit_prompt_action = "cancel"
                elif key in QUIT_KEYS:
                    state.exit_prompt_action = "cancel"
                continue

            if key in ESC_KEYS:
                if state.export_job and not state.export_job.dismissed:
                    state.export_job.dismissed = True
                    state.render_rev += 1
                    continue
                if not state.dirty:
                    break
                state.exit_prompt_visible = True
                state.exit_prompt_action = ""
                state.render_rev += 1
                continue

            if key in QUIT_KEYS:
                if not state.dirty:
                    break
                state.exit_prompt_visible = True
                state.exit_prompt_action = ""
                state.render_rev += 1
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