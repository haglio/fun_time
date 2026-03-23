from __future__ import annotations

from .controls import handle_key
from .exit_prompt import cycle_exit_prompt_focus, queue_exit_prompt_action, request_exit
from .paths import ENTER_KEYS, ESC_KEYS, QUIT_KEYS, TAB_KEYS


def should_redraw(
    state,
    *,
    loop_idx: int,
    last_loop_idx: int,
    now: float,
    last_present: float,
    max_redraw_hz: float = 30.0,
) -> bool:
    return state.render_rev > 0 or state.exit_prompt_visible or (
        loop_idx != last_loop_idx and (now - last_present) >= (1.0 / max_redraw_hz)
    )


def handle_window_close(state, *, reopen_window, request_exit_fn=request_exit) -> bool:
    if state.exit_prompt_visible:
        reopen_window()
        state.render_rev += 1
        return False
    if request_exit_fn(state):
        return True
    reopen_window()
    return False


def handle_ui_key(state, key: int, *, dispatch_key=handle_key, request_exit_fn=request_exit) -> bool:
    if key == -1:
        return False

    if state.exit_prompt_visible:
        if key in TAB_KEYS:
            cycle_exit_prompt_focus(state)
        elif key in ENTER_KEYS:
            queue_exit_prompt_action(state)
        elif key in ESC_KEYS or key in QUIT_KEYS:
            queue_exit_prompt_action(state, "cancel")
        return False

    if key in ESC_KEYS:
        if state.export_job and not state.export_job.dismissed:
            state.export_job.dismissed = True
            state.render_rev += 1
            return False
        return bool(request_exit_fn(state))

    if key in QUIT_KEYS:
        return bool(request_exit_fn(state))

    dispatch_key(state, key)
    return False
