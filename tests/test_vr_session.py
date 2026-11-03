"""The OpenXR session's decisions that need no headset: whether a located view
is worth drawing from, what the runtime's session states ask of the session, and
what each controller binds.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from types import SimpleNamespace

import pytest
import xr

from fun_time_vr.controller_bindings import (
    ACTION_TYPES,
    AIM,
    BACK,
    CONTROLLER_BINDINGS,
    FORWARD,
    STICK_CLICK,
    STICK_X,
    STICK_Y,
    TRIGGER,
)
from fun_time_vr.pointer import LEFT, RIGHT, HandInput
from fun_time_vr.view_tracking import views_are_renderable, views_are_tracked
from fun_time_vr.vr_session import VRSession


def test_a_fully_tracked_view_is_renderable():
    flags = (
        xr.ViewStateFlags.ORIENTATION_VALID_BIT
        | xr.ViewStateFlags.POSITION_VALID_BIT
        | xr.ViewStateFlags.ORIENTATION_TRACKED_BIT
        | xr.ViewStateFlags.POSITION_TRACKED_BIT
    )
    assert views_are_renderable(flags) is True


def test_orientation_alone_is_enough():
    assert views_are_renderable(xr.ViewStateFlags.ORIENTATION_VALID_BIT) is True


def test_an_unlocated_view_is_not_renderable():
    assert views_are_renderable(0) is False
    assert views_are_renderable(xr.ViewStateFlags.POSITION_VALID_BIT) is False


def test_renderable_is_not_the_same_question_as_tracked():
    valid_only = xr.ViewStateFlags.ORIENTATION_VALID_BIT

    assert views_are_renderable(valid_only) is True
    assert views_are_tracked(valid_only) is False
    assert views_are_tracked(valid_only | xr.ViewStateFlags.ORIENTATION_TRACKED_BIT) is True


def test_an_untracked_view_is_still_worth_drawing():
    assert views_are_renderable(xr.ViewStateFlags.ORIENTATION_VALID_BIT) is True


class _SessionStateEvents:
    def __init__(self, *states):
        self._queue = list(states)

    def __call__(self, _instance):
        if not self._queue:
            raise xr.EventUnavailable
        event = xr.EventDataSessionStateChanged(state=self._queue.pop(0))
        event.type = xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED
        return event


def _a_session_off_a_headset(monkeypatch, *states):
    session = VRSession.__new__(VRSession)
    session.running = True
    session._instance = object()
    session._session = object()
    session._session_state = xr.SessionState.UNKNOWN
    session._session_begun = False
    calls: list[str] = []
    monkeypatch.setattr(xr, "poll_event", _SessionStateEvents(*states))
    monkeypatch.setattr(xr, "begin_session", lambda *_a, **_kw: calls.append("begin"))
    monkeypatch.setattr(xr, "end_session", lambda *_a, **_kw: calls.append("end"))
    return session, calls


def test_a_session_the_runtime_has_readied_is_ready_to_submit_frames(monkeypatch):
    session, calls = _a_session_off_a_headset(monkeypatch, xr.SessionState.READY)

    session.poll_events()

    assert calls == ["begin"]
    assert session.session_ready is True


def test_a_stopped_session_is_no_longer_ready_to_submit_frames(monkeypatch):
    session, calls = _a_session_off_a_headset(
        monkeypatch, xr.SessionState.READY, xr.SessionState.STOPPING)

    session.poll_events()

    assert calls == ["begin", "end"]
    assert session.session_ready is False


def test_a_session_that_comes_back_is_ready_again(monkeypatch):
    session, _calls = _a_session_off_a_headset(
        monkeypatch, xr.SessionState.READY, xr.SessionState.STOPPING,
        xr.SessionState.READY)

    session.poll_events()

    assert session.session_ready is True


def test_each_hand_reads_both_axes_of_its_stick_and_both_skip_buttons(monkeypatch):
    session = VRSession.__new__(VRSession)
    session._session = object()
    session._actions = {name: name for name in
                        (AIM, TRIGGER, STICK_X, STICK_Y, STICK_CLICK, FORWARD, BACK)}
    readings = {TRIGGER: 0.2, STICK_X: 0.4, STICK_Y: -0.7, STICK_CLICK: True,
                FORWARD: True, BACK: False}

    def state(_session, get_info):
        return SimpleNamespace(is_active=True, current_state=readings[get_info.action])

    monkeypatch.setattr(xr, "ActionStateGetInfo",
                        lambda action, subaction_path: SimpleNamespace(action=action))
    monkeypatch.setattr(xr, "get_action_state_pose",
                        lambda *_args: SimpleNamespace(is_active=False))
    monkeypatch.setattr(xr, "get_action_state_float", state)
    monkeypatch.setattr(xr, "get_action_state_boolean", state)

    assert session._hand_input(RIGHT, object(), 0) == HandInput(
        trigger=0.2, stick_x=0.4, stick_y=-0.7, stick_pressed=True,
        forward=True, back=False)


class TestControllerBindings:
    @pytest.mark.parametrize("action, axis", [(STICK_X, "/x"), (STICK_Y, "/y")])
    def test_every_profile_with_a_stick_reads_both_its_axes_on_either_hand(self, action, axis):
        sticks = [bindings[action] for bindings in CONTROLLER_BINDINGS.values()
                  if STICK_Y in bindings]
        assert sticks, "no controller has a stick to read"
        for paths in sticks:
            assert {path.split("/")[3] for path in paths} == {LEFT, RIGHT}
            assert all(path.endswith(axis) for path in paths)

    def test_a_controller_with_a_stick_also_reads_it_pushed_in(self):
        for profile, bindings in CONTROLLER_BINDINGS.items():
            if STICK_Y not in bindings:
                continue
            paths = bindings[STICK_CLICK]
            assert {path.split("/")[3] for path in paths} == {LEFT, RIGHT}, profile
            assert all(path.endswith("/click") for path in paths), profile

    def test_every_profile_points_and_squeezes_with_either_hand(self):
        for profile, bindings in CONTROLLER_BINDINGS.items():
            assert profile.startswith("/interaction_profiles/")
            for action in (AIM, TRIGGER):
                hands = {path.split("/")[3] for path in bindings[action]}
                assert hands == {LEFT, RIGHT}, f"{profile} binds {action} for {hands}"
            assert all(path.endswith("/aim/pose") for path in bindings[AIM])

    def test_a_skips_forward_and_b_skips_back_on_every_controller_that_has_them(self):
        touch = CONTROLLER_BINDINGS["/interaction_profiles/oculus/touch_controller"]
        index = CONTROLLER_BINDINGS["/interaction_profiles/valve/index_controller"]

        assert set(touch[FORWARD]) == {
            "/user/hand/right/input/a/click", "/user/hand/left/input/x/click"}
        assert set(touch[BACK]) == {
            "/user/hand/right/input/b/click", "/user/hand/left/input/y/click"}
        assert set(index[FORWARD]) == {
            "/user/hand/right/input/a/click", "/user/hand/left/input/a/click"}
        assert set(index[BACK]) == {
            "/user/hand/right/input/b/click", "/user/hand/left/input/b/click"}

    def test_every_action_a_controller_binds_is_one_the_session_creates(self):
        bound = {name for bindings in CONTROLLER_BINDINGS.values() for name in bindings}

        assert bound <= set(ACTION_TYPES)

    def test_the_suite_covers_the_headsets_this_family_meets(self):
        profiles = set(CONTROLLER_BINDINGS)
        assert any("oculus" in p for p in profiles)
        assert any("valve/index" in p for p in profiles)
        assert any("htc/vive" in p for p in profiles)


def test_the_scene_window_wears_the_sessions_button_before_it_reaches_the_taskbar():
    """Its thread is off bringing up OpenXR the moment it is shown, too busy to
    tell the taskbar its icon -- so what the button shows has to be on the
    window already, read off it rather than asked of it."""
    calls = {ast.unparse(node.func): node.lineno
             for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(VRSession._init_glfw))))
             if isinstance(node, ast.Call)}

    assert calls["preview_marker.dress_the_window"] < calls["minimize_window"]
