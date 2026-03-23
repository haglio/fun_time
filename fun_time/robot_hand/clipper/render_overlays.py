from __future__ import annotations

from typing import Callable

import cv2
import numpy as np

from .exit_prompt import EXIT_PROMPT_BUTTON_NAMES, EXIT_PROMPT_CHOICES
from .state import VideoState

DrawButton = Callable[..., None]
DrawProgressBar = Callable[..., None]
PutText = Callable[..., None]


def draw_export_overlay(
    canvas: np.ndarray,
    state: VideoState,
    *,
    draw_button: DrawButton,
    draw_progress_bar: DrawProgressBar,
    put_text: PutText,
) -> None:
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


def draw_exit_overlay(
    canvas: np.ndarray,
    state: VideoState,
    *,
    draw_button: DrawButton,
    put_text: PutText,
) -> None:
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
