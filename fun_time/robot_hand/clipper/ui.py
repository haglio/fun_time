from __future__ import annotations

import time

import cv2

from .controls import handle_key, on_mouse
from .exit_prompt import (
    cycle_exit_prompt_focus,
    finish_exit_prompt_action,
    queue_exit_prompt_action,
    request_exit,
)
from .paths import (
    ENTER_KEYS,
    ESC_KEYS,
    QUIT_KEYS,
    TAB_KEYS,
)
from .render import build_ui
from .state import (
    VideoState,
    current_loop_frame_index,
)
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
            need_redraw = state.render_rev > 0 or state.exit_prompt_visible or (loop_idx != last_loop_idx and (now - last_present) >= (1.0 / 30.0))
            if need_redraw:
                last_loop_idx = loop_idx
                last_present = now
                state.render_rev = 0
                ui = build_ui(state)
                cv2.imshow(window_name, ui)

            if window_closed(window_name):
                if state.exit_prompt_visible:
                    ensure_window(window_name, state, mouse_callback=on_mouse)
                    state.render_rev += 1
                    continue
                if request_exit(state):
                    break
                ensure_window(window_name, state, mouse_callback=on_mouse)
                continue

            key = cv2.waitKeyEx(20)

            if window_closed(window_name):
                if state.exit_prompt_visible:
                    ensure_window(window_name, state, mouse_callback=on_mouse)
                    state.render_rev += 1
                    continue
                if request_exit(state):
                    break
                ensure_window(window_name, state, mouse_callback=on_mouse)
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
                if request_exit(state):
                    break
                continue

            if key in QUIT_KEYS:
                if request_exit(state):
                    break
                continue

            handle_key(state, key)
    finally:
        cleanup_window(window_name, state, sleep=time.sleep)
