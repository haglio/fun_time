from __future__ import annotations

from typing import Any

import cv2

from .exit_prompt import EXIT_PROMPT_BUTTON_NAMES, EXIT_PROMPT_CHOICES, queue_exit_prompt_action
from .export import start_export_job
from .navigation import (
    index_for_timeline_x,
    move_current_left,
    move_current_right,
    toggle_wrap_mode,
)
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
    SHIFT_RANGE_LEFT_KEYS,
    SHIFT_RANGE_RIGHT_KEYS,
    SPEED_DOWN_KEYS,
    SPEED_UP_KEYS,
    WIN_LEFT_KEYS,
    WIN_RIGHT_KEYS,
    WRAP_TOGGLE_KEYS,
)
from .playback import change_speed, toggle_loop_pause
from .state import (
    VideoState,
    accept_suggested_in,
    accept_suggested_out,
    contract_left,
    contract_right,
    cycle_loop_mode,
    extend_left,
    extend_right,
    set_mark_in,
    set_mark_out,
    shift_active_range,
)

Rect = tuple[int, int, int, int]


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
