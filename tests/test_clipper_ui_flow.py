from __future__ import annotations

from types import SimpleNamespace

from fun_time.robot_hand.clipper.ui_flow import handle_ui_key, handle_window_close, should_redraw


def test_should_redraw_when_render_revision_is_pending():
    state = SimpleNamespace(render_rev=1, exit_prompt_visible=False)

    assert should_redraw(state, loop_idx=3, last_loop_idx=3, now=10.0, last_present=10.0) is True


def test_should_redraw_when_exit_prompt_is_visible():
    state = SimpleNamespace(render_rev=0, exit_prompt_visible=True)

    assert should_redraw(state, loop_idx=3, last_loop_idx=3, now=10.0, last_present=10.0) is True


def test_should_redraw_throttles_loop_animation_to_target_rate():
    state = SimpleNamespace(render_rev=0, exit_prompt_visible=False)

    assert should_redraw(state, loop_idx=4, last_loop_idx=3, now=10.1, last_present=10.0) is True
    assert should_redraw(state, loop_idx=4, last_loop_idx=3, now=10.01, last_present=10.0) is False


def test_handle_window_close_reopens_when_exit_prompt_is_visible():
    state = SimpleNamespace(exit_prompt_visible=True, render_rev=0)
    reopens: list[str] = []

    result = handle_window_close(state, reopen_window=lambda: reopens.append("reopen"))

    assert result is False
    assert reopens == ["reopen"]
    assert state.render_rev == 1


def test_handle_window_close_returns_true_when_exit_is_confirmed():
    state = SimpleNamespace(exit_prompt_visible=False, render_rev=0)

    result = handle_window_close(state, reopen_window=lambda: None, request_exit_fn=lambda _state: True)

    assert result is True


def test_handle_ui_key_routes_exit_prompt_keys():
    state = SimpleNamespace(exit_prompt_visible=True, exit_prompt_focus="save", exit_prompt_action="", render_rev=0)

    assert handle_ui_key(state, 9) is False
    assert state.exit_prompt_focus == "discard"

    assert handle_ui_key(state, 27) is False
    assert state.exit_prompt_focus == "cancel"
    assert state.exit_prompt_action == "cancel"


def test_handle_ui_key_dismisses_export_overlay_before_requesting_exit():
    export_job = SimpleNamespace(dismissed=False)
    state = SimpleNamespace(exit_prompt_visible=False, export_job=export_job, render_rev=0)

    result = handle_ui_key(state, 27, request_exit_fn=lambda _state: True)

    assert result is False
    assert export_job.dismissed is True
    assert state.render_rev == 1


def test_handle_ui_key_requests_exit_for_quit_keys():
    state = SimpleNamespace(exit_prompt_visible=False, export_job=None, render_rev=0)

    result = handle_ui_key(state, ord("q"), request_exit_fn=lambda _state: True)

    assert result is True


def test_handle_ui_key_dispatches_regular_keys():
    state = SimpleNamespace(exit_prompt_visible=False, export_job=None, render_rev=0)
    dispatched: list[int] = []

    result = handle_ui_key(state, ord("l"), dispatch_key=lambda _state, key: dispatched.append(key))

    assert result is False
    assert dispatched == [ord("l")]
