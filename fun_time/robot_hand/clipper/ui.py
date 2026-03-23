from __future__ import annotations

import time

import cv2

from .controls import handle_key, on_mouse
from .exit_prompt import (
    cycle_exit_prompt_focus,
    finish_exit_prompt_action,
    queue_exit_prompt_action,
    request_exit,
    show_exit_prompt,
)
from .export import terminate_export_subprocesses
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
from .window_icons import set_cv2_window_icon

APP_DISPLAY_NAME = "Clipper"

def _window_closed(window_name: str) -> bool:
    try:
        return cv2.getWindowProperty(window_name, cv2.WND_PROP_VISIBLE) < 1
    except cv2.error:
        return True


def run_ui(state: VideoState) -> None:
    window_name = APP_DISPLAY_NAME

    def ensure_window() -> None:
        cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(window_name, 1520, 960)
        set_cv2_window_icon(window_name)
        cv2.setMouseCallback(window_name, on_mouse, state)

    ensure_window()
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

            if _window_closed(window_name):
                if state.exit_prompt_visible:
                    ensure_window()
                    state.render_rev += 1
                    continue
                if request_exit(state):
                    break
                ensure_window()
                continue

            key = cv2.waitKeyEx(20)

            if _window_closed(window_name):
                if state.exit_prompt_visible:
                    ensure_window()
                    state.render_rev += 1
                    continue
                if request_exit(state):
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
                if request_exit(state):
                    break
                continue

            if key in QUIT_KEYS:
                if request_exit(state):
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
