from __future__ import annotations

import ast
import inspect
import logging
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
from fun_time_vr.controller_reading import ControllerActions, no_hands
from fun_time_vr.pointer import LEFT, RIGHT, HandInput
from fun_time_vr.retiring import RETIRE_AFTER_FRAMES, QuadSwapchains
from fun_time_vr.session_states import SessionStates
from fun_time_vr.view_tracking import (
    TrackedViews,
    layers_to_submit,
    views_are_renderable,
    views_are_tracked,
)
from fun_time_vr.vr_session import SwapchainInfo, VRSession


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


def _states():
    calls: list[str] = []
    return SessionStates(begin=lambda: calls.append("begin"),
                         end=lambda: calls.append("end")), calls


def test_a_session_the_runtime_has_readied_is_ready_to_submit_frames():
    states, calls = _states()

    states.heard(xr.SessionState.READY)

    assert calls == ["begin"]
    assert states.begun is True


def test_a_stopped_session_is_no_longer_ready_to_submit_frames():
    states, calls = _states()

    states.heard(xr.SessionState.READY)
    states.heard(xr.SessionState.STOPPING)

    assert calls == ["begin", "end"]
    assert states.begun is False


def test_a_session_that_comes_back_is_ready_again():
    states, _calls = _states()

    for state in (xr.SessionState.READY, xr.SessionState.STOPPING, xr.SessionState.READY):
        states.heard(state)

    assert states.begun is True


@pytest.mark.parametrize("state", [xr.SessionState.LOSS_PENDING, xr.SessionState.EXITING])
def test_a_session_the_runtime_is_taking_away_stops_running(state):
    states, calls = _states()

    states.heard(state)

    assert states.running is False
    assert calls == []


@pytest.mark.parametrize(("state", "draws", "focused", "asked_to_end"), [
    (xr.SessionState.UNKNOWN, False, False, False),
    (xr.SessionState.READY, False, False, True),
    (xr.SessionState.SYNCHRONIZED, False, False, True),
    (xr.SessionState.VISIBLE, True, False, True),
    (xr.SessionState.FOCUSED, True, True, True),
    (xr.SessionState.STOPPING, False, False, False),
])
def test_what_each_state_lets_the_session_do(state, draws, focused, asked_to_end):
    states, _calls = _states()

    states.heard(state)

    assert (states.draws_frames, states.focused, states.can_be_asked_to_end) == (
        draws, focused, asked_to_end)


_TRACKED = xr.ViewStateFlags.ORIENTATION_VALID_BIT | xr.ViewStateFlags.ORIENTATION_TRACKED_BIT


class TestTheViewsToDraw:
    def test_views_the_runtime_located_are_drawn(self):
        views = TrackedViews()

        assert views.to_draw(_TRACKED, ("left", "right")) == ["left", "right"]
        assert views.tracked is True

    def test_views_it_cannot_locate_are_not_drawn_and_not_tracked(self):
        views = TrackedViews()

        assert views.to_draw(0, ("left", "right")) == []
        assert views.tracked is False

    def test_views_located_but_untracked_are_still_drawn(self):
        views = TrackedViews()

        assert views.to_draw(xr.ViewStateFlags.ORIENTATION_VALID_BIT, ("left",)) == ["left"]
        assert views.tracked is False

    def test_losing_and_finding_the_views_is_said_once_each(self, caplog):
        views = TrackedViews()

        with caplog.at_level(logging.INFO):
            for flags in (_TRACKED, 0, 0, _TRACKED, _TRACKED):
                views.to_draw(flags, ("left",))

        assert [record.levelname for record in caplog.records] == ["WARNING", "INFO"]


class TestTheLayersOfAFrame:
    def test_a_frame_shows_the_scene_under_its_quads(self):
        assert layers_to_submit(("left",), project=True, projection="scene",
                                quads=("a", "b")) == ["scene", "a", "b"]

    def test_a_frame_of_only_quads_leaves_the_scene_out(self):
        assert layers_to_submit(("left",), project=False, projection="scene",
                                quads=("a",)) == ["a"]

    def test_a_frame_with_no_views_submits_nothing_at_all(self):
        assert layers_to_submit((), project=True, projection="scene", quads=("a",)) == []


class _Swapchain:
    def __init__(self, handle, width, height):
        self.handle, self.width, self.height = handle, width, height


class TestTheQuadSwapchains:
    def _quads(self):
        made, destroyed = [], []

        def make(width, height):
            made.append((width, height))
            return _Swapchain(f"chain{len(made)}", width, height)

        return QuadSwapchains(make=make, destroy=destroyed.append), made, destroyed

    def test_the_first_ask_makes_one_and_says_so(self):
        quads, made, _destroyed = self._quads()

        assert quads.ensure(0, 640, 480) is True
        assert made == [(640, 480)]
        assert quads[0].handle == "chain1"

    def test_the_same_size_again_keeps_the_one_it_has(self):
        quads, made, _destroyed = self._quads()
        quads.ensure(0, 640, 480)

        assert quads.ensure(0, 640, 480) is False
        assert made == [(640, 480)]

    def test_a_new_size_retires_the_old_one_only_after_frames_that_may_still_show_it(self):
        quads, _made, destroyed = self._quads()
        quads.ensure(0, 640, 480)
        quads.ensure(0, 800, 600)

        for _frame in range(RETIRE_AFTER_FRAMES - 1):
            quads.frame_ended()
        assert destroyed == []

        quads.frame_ended()
        assert destroyed == ["chain1"]

    def test_closing_destroys_the_current_ones_and_those_still_retiring(self):
        quads, _made, destroyed = self._quads()
        quads.ensure(0, 640, 480)
        quads.ensure(0, 800, 600)

        quads.close()

        assert destroyed == ["chain2", "chain1"]


A_HAND = object()
_ALL_ACTIONS = (AIM, TRIGGER, STICK_X, STICK_Y, STICK_CLICK, FORWARD, BACK)
_FULLY_LOCATED = (xr.SpaceLocationFlags.ORIENTATION_VALID_BIT
                  | xr.SpaceLocationFlags.POSITION_VALID_BIT)


class _ActionState:
    def __init__(self, *, is_active, current_state=0):
        self.is_active, self.current_state = is_active, current_state


class _Point:
    x, y, z, w = 1.0, 2.0, 3.0, 4.0


class _Pose:
    position = orientation = _Point


class _AimLocation:
    pose = _Pose

    def __init__(self, location_flags):
        self.location_flags = location_flags


class _FakeRuntime:
    def __init__(self, readings=None, *, active=True, aim=None, refuses=False):
        self._readings = readings or {}
        self._active = active
        self._aim = aim
        self._refuses = refuses

    def ActionsSyncInfo(self, **_kw):
        return None

    def ActiveActionSet(self, **_kw):
        return None

    def ActionStateGetInfo(self, *, action, subaction_path):
        return action

    def sync_actions(self, *_a):
        if self._refuses:
            raise xr.ResultException

    def get_action_state_float(self, _session, action):
        return _ActionState(is_active=self._active, current_state=self._readings.get(action, 0))

    get_action_state_boolean = get_action_state_float

    def get_action_state_pose(self, *_a):
        return _ActionState(is_active=self._aim is not None)

    def locate_space(self, *_a):
        return self._aim


def _controllers(runtime):
    return ControllerActions(object(), object(), {name: name for name in _ALL_ACTIONS},
                             ((RIGHT, A_HAND),), {RIGHT: object()}, runtime)


def test_each_hand_reads_both_axes_of_its_stick_and_both_skip_buttons():
    readings = {TRIGGER: 0.2, STICK_X: 0.4, STICK_Y: -0.7, STICK_CLICK: True,
                FORWARD: True, BACK: False}

    assert _controllers(_FakeRuntime(readings)).read(0, object()) == {RIGHT: HandInput(
        trigger=0.2, stick_x=0.4, stick_y=-0.7, stick_pressed=True, forward=True, back=False)}


def test_a_controller_that_is_off_reads_as_untouched():
    runtime = _FakeRuntime({TRIGGER: 0.9, FORWARD: True}, active=False)

    assert _controllers(runtime).read(0, object()) == {RIGHT: HandInput()}


def test_a_hand_the_runtime_has_located_points():
    hands = _controllers(_FakeRuntime(aim=_AimLocation(_FULLY_LOCATED))).read(0, object())

    assert hands[RIGHT].aim == ((1.0, 2.0, 3.0), (1.0, 2.0, 3.0, 4.0))


def test_a_hand_located_only_in_part_does_not_point():
    aim = _AimLocation(xr.SpaceLocationFlags.POSITION_VALID_BIT)

    assert _controllers(_FakeRuntime(aim=aim)).read(0, object())[RIGHT].aim is None


def test_controllers_the_runtime_will_not_read_read_as_no_hands():
    assert _controllers(_FakeRuntime(refuses=True)).read(0, object()) == no_hands()



class TestControllerBindings:
    @pytest.mark.parametrize(("action", "axis"), [(STICK_X, "/x"), (STICK_Y, "/y")])
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
    calls = {ast.unparse(node.func): node.lineno
             for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(VRSession._init_glfw))))
             if isinstance(node, ast.Call)}

    assert calls["preview_marker.dress_the_window"] < calls["minimize_window"]


class _SessionStateEvents:
    def __init__(self, *states):
        self._queue = list(states)

    def __call__(self, _instance):
        if not self._queue:
            raise xr.EventUnavailable
        event = xr.EventDataSessionStateChanged(state=self._queue.pop(0))
        event.type = xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED
        return event


def _a_session_off_a_headset(monkeypatch):
    session = VRSession.__new__(VRSession)
    session._instance = object()
    session._session = object()
    session._space = xr.Space()
    views = TrackedViews()
    session._views = views
    session._views_to_draw = views.to_draw
    session._period_logged = True
    session.swapchains = [SwapchainInfo(xr.Swapchain(), 640, 480)]
    calls: list[str] = []
    quads = QuadSwapchains(make=lambda w, h: SwapchainInfo(xr.Swapchain(), w, h),
                           destroy=lambda _handle: calls.append("destroyed"))
    session._quads = quads
    session._quads_frame_ended = quads.frame_ended
    SessionStates.__init__(session, begin=session._begin_session, end=session._end_session)
    monkeypatch.setattr(xr, "begin_session", lambda *_a, **_kw: calls.append("begin"))
    monkeypatch.setattr(xr, "end_session", lambda *_a, **_kw: calls.append("end"))
    return session, calls


def test_what_the_runtime_reports_reaches_the_sessions_state(monkeypatch):
    session, calls = _a_session_off_a_headset(monkeypatch)
    monkeypatch.setattr(xr, "poll_event", _SessionStateEvents(
        xr.SessionState.READY, xr.SessionState.FOCUSED))

    session.poll_events()

    assert calls == ["begin"]
    assert (session.session_ready, session.focused, session.running) == (True, True, True)


def _the_layers_it_ends_a_frame_with(session, monkeypatch, views, **kwargs):
    ended = []
    monkeypatch.setattr(xr, "end_frame", lambda _session, info: ended.append(info))
    session.frame_end(7, views, **kwargs)
    return ended[0].layer_count


def test_a_frame_with_views_ends_with_the_scene_on_the_headset(monkeypatch):
    session, _calls = _a_session_off_a_headset(monkeypatch)

    assert _the_layers_it_ends_a_frame_with(session, monkeypatch, [xr.View()]) == 1


def test_a_frame_with_no_views_ends_with_no_layers(monkeypatch):
    session, _calls = _a_session_off_a_headset(monkeypatch)

    assert _the_layers_it_ends_a_frame_with(session, monkeypatch, []) == 0


def test_a_frame_with_a_quad_submits_it_and_lets_old_quads_retire(monkeypatch):
    session, calls = _a_session_off_a_headset(monkeypatch)
    session._quads.ensure(0, 100, 100)
    session._quads.ensure(0, 200, 100)
    quad = SimpleNamespace(swapchain_index=0, orientation=(0, 0, 0, 1), position=(0, 0, 0),
                           size=(1, 1))

    for _frame in range(RETIRE_AFTER_FRAMES):
        layers = _the_layers_it_ends_a_frame_with(
            session, monkeypatch, [xr.View()], project=False, quads=[quad])

    assert layers == 1
    assert calls == ["destroyed"]


def _a_frame_starting(monkeypatch):
    session, _calls = _a_session_off_a_headset(monkeypatch)
    monkeypatch.setattr(xr, "wait_frame", lambda *_a: SimpleNamespace(
        should_render=True, predicted_display_period=0, predicted_display_time=5))
    monkeypatch.setattr(xr, "begin_frame", lambda *_a: None)
    return session


def test_a_frame_the_runtime_will_not_show_locates_no_views(monkeypatch):
    session = _a_frame_starting(monkeypatch)

    assert session.frame_begin() == (False, 5, [])


def test_a_frame_the_runtime_shows_draws_the_views_it_located(monkeypatch):
    session = _a_frame_starting(monkeypatch)
    session.heard(xr.SessionState.FOCUSED)
    monkeypatch.setattr(xr, "locate_views", lambda *_a: (
        SimpleNamespace(view_state_flags=_TRACKED), ("left", "right")))

    assert session.frame_begin() == (True, 5, ["left", "right"])
    assert session.views_tracked is True
