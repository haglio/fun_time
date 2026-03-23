from __future__ import annotations

import time
from typing import Any

import cv2

from .exit_prompt import (
    EXIT_PROMPT_BUTTON_NAMES,
    EXIT_PROMPT_CHOICES,
    cycle_exit_prompt_focus,
    finish_exit_prompt_action,
    queue_exit_prompt_action,
    request_exit,
    show_exit_prompt,
)
from .export import start_export_job, terminate_export_subprocesses
from .paths import (
    ACCEPT_SUGGESTED_IN_KEYS,
    ACCEPT_SUGGESTED_OUT_KEYS,
    BOUNDS_CONTRACT_LEFT_KEYS,
    BOUNDS_CONTRACT_RIGHT_KEYS,
    BOUNDS_EXTEND_LEFT_KEYS,
    BOUNDS_EXTEND_RIGHT_KEYS,
    ENTER_KEYS,
    ESC_KEYS,
    LOOP_MODE_CYCLE_KEYS,
    MARK_IN_KEYS,
    MARK_OUT_KEYS,
    PLAY_PAUSE_KEYS,
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
from .render import build_ui
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
    move_current_left,
    move_current_right,
    set_mark_in,
    set_mark_out,
    shift_active_range,
    toggle_loop_pause,
    toggle_wrap_mode,
)
from .window_icons import set_cv2_window_icon

Rect = tuple[int, int, int, int]
APP_DISPLAY_NAME = "Clipper"


def point_in_rect(x: int, y: int, rect: Rect) -> bool:
    x1, y1, x2, y2 = rect
    return x1 <= x <= x2 and y1 <= y <= y2

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
