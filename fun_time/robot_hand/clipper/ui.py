from __future__ import annotations

import time

import cv2

from .controls import handle_key, on_mouse
from .exit_prompt import finish_exit_prompt_action
from .render import build_ui
from .state import (
    VideoState,
    current_loop_frame_index,
)
from .ui_flow import handle_ui_key, handle_window_close, should_redraw
from .window_runtime import cleanup_window, ensure_window, window_closed

APP_DISPLAY_NAME = "Clipper"


def run_ui(state: VideoState) -> None:
    window_name = APP_DISPLAY_NAME
    ensure_window(window_name, state, mouse_callback=on_mouse)
    last_loop_idx = -1
    last_present = 0.0

    try:
        while True:
            if state.exit_prompt_action:
                if finish_exit_prompt_action(state, state.exit_prompt_action):
                    break
                continue

            loop_idx = current_loop_frame_index(state)
            now = time.monotonic()
            if should_redraw(
                state,
                loop_idx=loop_idx,
                last_loop_idx=last_loop_idx,
                now=now,
                last_present=last_present,
            ):
                last_loop_idx = loop_idx
                last_present = now
                state.render_rev = 0
                ui = build_ui(state)
                cv2.imshow(window_name, ui)

            if window_closed(window_name):
                if handle_window_close(
                    state,
                    reopen_window=lambda: ensure_window(window_name, state, mouse_callback=on_mouse),
                ):
                    break
                continue

            key = cv2.waitKeyEx(20)

            if window_closed(window_name):
                if handle_window_close(
                    state,
                    reopen_window=lambda: ensure_window(window_name, state, mouse_callback=on_mouse),
                ):
                    break
                continue

            if handle_ui_key(state, key, dispatch_key=handle_key):
                break
    finally:
        cleanup_window(window_name, state, sleep=time.sleep)
