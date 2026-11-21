from __future__ import annotations

import logging
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
from fun_time_vr.vr_session import VRSession
from tests.openxr_fakes import (
    EYE_SIZE,
    IMAGES_PER_SWAPCHAIN,
    FakeOpenXR,
    FakeSceneWindow,
    address_of,
    recentered,
    state_changed,
)


def test_a_fully_tracked_view_is_renderable():
    flags = (
        xr.ViewStateFlags.ORIENTATION_VALID_BIT
        | xr.ViewStateFlags.POSITION_VALID_BIT
        | xr.ViewStateFlags.ORIENTATION_TRACKED_BIT
        | xr.ViewStateFlags.POSITION_TRACKED_BIT
    )
    assert views_are_renderable(flags) is True


def test_a_dim_room_that_loses_position_still_draws_from_orientation():
    assert views_are_renderable(xr.ViewStateFlags.ORIENTATION_VALID_BIT) is True


def test_an_unlocated_view_is_not_renderable():
    assert views_are_renderable(0) is False
    assert views_are_renderable(xr.ViewStateFlags.POSITION_VALID_BIT) is False


def test_a_pose_the_runtime_only_guessed_is_valid_and_follows_no_head():
    a_guessed_pose = xr.ViewStateFlags.ORIENTATION_VALID_BIT

    assert views_are_renderable(a_guessed_pose) is True
    assert views_are_tracked(a_guessed_pose) is False
    assert views_are_tracked(a_guessed_pose | xr.ViewStateFlags.ORIENTATION_TRACKED_BIT) is True


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


def test_a_headset_worn_again_readies_the_stopped_session_once_more():
    states, _calls = _states()
    what_the_runtime_says_when_it_is_worn_again = xr.SessionState.READY

    for state in (xr.SessionState.READY, xr.SessionState.STOPPING,
                  what_the_runtime_says_when_it_is_worn_again):
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

    def test_losing_and_finding_the_views_is_said_once_each_and_never_as_a_warning(self, caplog):
        views = TrackedViews()

        with caplog.at_level(logging.INFO):
            for flags in (_TRACKED, 0, 0, _TRACKED, _TRACKED):
                views.to_draw(flags, ("left",))

        assert [record.levelname for record in caplog.records] == ["INFO", "INFO"]


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


def _a_session(*, journal=None, **runtime_kwargs):
    journal = [] if journal is None else journal
    runtime = FakeOpenXR(journal, **runtime_kwargs)
    scene = FakeSceneWindow(journal)
    return VRSession(runtime=runtime, scene=scene), runtime, scene


def _the_bindings_each_profile_suggests() -> dict[str, list[tuple[str, str]]]:
    return {profile: sorted((name, path) for name, paths in bindings.items() for path in paths)
            for profile, bindings in CONTROLLER_BINDINGS.items()}


class TestEnteringVR:
    def test_the_scene_window_is_up_before_openxr_takes_the_thread(self):
        journal: list[str] = []
        _a_session(journal=journal)

        assert journal.index("open the scene window Fun Time VR") < journal.index("create Instance")

    def test_the_runtime_is_asked_what_opengl_it_needs_before_the_session_is_made(self):
        _session, runtime, _scene = _a_session()

        assert (runtime.journal.index("ask what OpenGL it needs")
                < runtime.journal.index("create Session"))

    def test_each_eye_gets_a_swapchain_the_size_the_runtime_recommends(self):
        session, _runtime, _scene = _a_session()

        assert [(info.width, info.height, len(info.images)) for info in session.swapchains] == [
            (*EYE_SIZE, IMAGES_PER_SWAPCHAIN)] * 2

    def test_the_funestras_share_the_scene_windows_contexts(self):
        session, _runtime, scene = _a_session()

        assert session.shared_contexts() is scene.contexts

    def test_a_bring_up_that_fails_part_way_leaves_nothing_for_the_retry(self):
        journal: list[str] = []
        with pytest.raises(xr.ResultException):
            _a_session(journal=journal, refuses={"create Swapchain"})

        runtime_left = [line for line in journal if line.startswith("destroy")]
        assert runtime_left == ["destroy Space", "destroy Space", "destroy Space",
                                "destroy Session", "destroy Instance"]
        assert journal[-1] == "close the scene window"

    def test_a_bring_up_whose_teardown_also_fails_still_says_why_it_failed(self):
        with pytest.raises(xr.ResultException, match="create Swapchain"):
            _a_session(refuses={"create Swapchain", "destroy Session"})


class TestTheControllerBindings:
    def test_every_controller_the_family_meets_is_offered_its_bindings(self):
        _session, runtime, _scene = _a_session()

        assert runtime.suggested == _the_bindings_each_profile_suggests()

    def test_a_controller_the_runtime_does_not_know_leaves_the_others_bound(self):
        refused = "/interaction_profiles/htc/vive_controller"

        _session, runtime, _scene = _a_session(refuses_profiles={refused})

        expected = _the_bindings_each_profile_suggests()
        del expected[refused]
        assert runtime.suggested == expected
        assert runtime.attached == [1]

    def test_either_hand_aims_from_a_space_of_its_own(self):
        _session, runtime, _scene = _a_session()

        made_after_the_room = runtime.journal[runtime.journal.index("create ActionSet"):]
        assert made_after_the_room.count("create Space") == 2

    def test_a_runtime_that_will_not_take_the_controls_leaves_the_keys_and_voice(self, caplog):
        with caplog.at_level(logging.WARNING, logger="fun_time_vr.vr_session"):
            session, _runtime, _scene = _a_session(refuses={"attach the controls"})

        session.sync_controller(5)

        assert session.hands == no_hands()
        assert "the keys and voice still work" in caplog.text

    def test_the_controllers_are_read_through_the_same_runtime(self):
        session, _runtime, _scene = _a_session()

        session.sync_controller(5)

        assert session.hands == {LEFT: HandInput(), RIGHT: HandInput()}


def _the_runtime_reports(session, runtime, *states):
    runtime.events.extend(state_changed(state) for state in states)
    session.poll_events()


def _sessions_begun_and_ended(runtime) -> list[str]:
    return [line for line in runtime.journal if line in ("begin the session", "end the session")]


class TestTakingTheHeadsetOffAndOn:
    def test_a_headset_put_on_begins_the_session_and_draws_for_the_wearer(self):
        session, runtime, _scene = _a_session()

        _the_runtime_reports(session, runtime, xr.SessionState.READY,
                             xr.SessionState.SYNCHRONIZED, xr.SessionState.VISIBLE,
                             xr.SessionState.FOCUSED)

        assert _sessions_begun_and_ended(runtime) == ["begin the session"]
        assert (session.session_ready, session.focused, session.draws_frames) == (True, True, True)

    def test_a_headset_taken_off_ends_the_session_but_keeps_it_running(self):
        session, runtime, _scene = _a_session()
        _the_runtime_reports(session, runtime, xr.SessionState.READY, xr.SessionState.FOCUSED)

        _the_runtime_reports(session, runtime, xr.SessionState.VISIBLE,
                             xr.SessionState.SYNCHRONIZED, xr.SessionState.STOPPING)

        assert _sessions_begun_and_ended(runtime) == ["begin the session", "end the session"]
        assert (session.session_ready, session.focused, session.running) == (False, False, True)

    def test_a_headset_put_back_on_begins_the_session_again(self):
        session, runtime, _scene = _a_session()
        _the_runtime_reports(session, runtime, xr.SessionState.READY, xr.SessionState.FOCUSED,
                             xr.SessionState.STOPPING)

        _the_runtime_reports(session, runtime, xr.SessionState.READY, xr.SessionState.FOCUSED)

        assert _sessions_begun_and_ended(runtime) == [
            "begin the session", "end the session", "begin the session"]
        assert (session.session_ready, session.focused) == (True, True)

    def test_a_headset_lost_to_the_runtime_stops_the_session(self):
        session, runtime, _scene = _a_session()

        _the_runtime_reports(session, runtime, xr.SessionState.READY,
                             xr.SessionState.LOSS_PENDING)

        assert session.running is False

    def test_a_recenter_swaps_the_room_for_a_new_space_and_frees_the_old(self):
        session, runtime, _scene = _a_session()
        spaces_before = runtime.kinds_living().count("Space")
        runtime.events.append(recentered())

        session.poll_events()

        assert runtime.journal[-2:] == ["create Space", "destroy Space"]
        assert runtime.kinds_living().count("Space") == spaces_before


class TestLeavingVR:
    def test_closing_frees_everything_the_session_made(self):
        session, runtime, _scene = _a_session()
        session.ensure_quad_swapchain(0, 200, 100)

        session.close()

        assert runtime.kinds_living() == []

    def test_closing_a_running_session_asks_it_to_exit_first(self):
        session, runtime, _scene = _a_session()
        _the_runtime_reports(session, runtime, xr.SessionState.READY, xr.SessionState.FOCUSED)

        session.close()

        closing = runtime.journal[runtime.journal.index("ask the session to exit"):]
        assert closing[1:] == ["destroy Swapchain", "destroy Swapchain", "destroy Space",
                               "destroy Space", "destroy Space", "destroy Session",
                               "destroy Instance", "close the scene window"]

    def test_a_session_the_runtime_has_already_ended_is_not_asked_to_exit(self):
        session, runtime, _scene = _a_session()
        _the_runtime_reports(session, runtime, xr.SessionState.READY, xr.SessionState.EXITING)

        session.close()

        assert "ask the session to exit" not in runtime.journal
        assert runtime.kinds_living() == []

    def test_a_runtime_that_refuses_the_exit_is_closed_all_the_same(self):
        session, runtime, _scene = _a_session(refuses={"ask the session to exit"})
        _the_runtime_reports(session, runtime, xr.SessionState.READY)

        session.close()

        assert runtime.kinds_living() == []


def _a_session_drawing(**runtime_kwargs):
    session, runtime, scene = _a_session(**runtime_kwargs)
    _the_runtime_reports(session, runtime, xr.SessionState.READY, xr.SessionState.FOCUSED)
    return session, runtime, scene


def _the_layers_it_ends_a_frame_with(session, runtime, views, **kwargs):
    session.frame_end(7, views, **kwargs)
    return runtime.ended_frames[-1].layer_count


def test_a_frame_with_views_ends_with_the_scene_on_the_headset():
    session, runtime, _scene = _a_session_drawing()

    assert _the_layers_it_ends_a_frame_with(session, runtime, [xr.View()]) == 1


def test_a_frame_with_no_views_ends_with_no_layers():
    session, runtime, _scene = _a_session_drawing()

    assert _the_layers_it_ends_a_frame_with(session, runtime, []) == 0


def test_a_frame_with_a_quad_submits_it_and_lets_old_quads_retire():
    session, runtime, _scene = _a_session_drawing()
    session.ensure_quad_swapchain(0, 100, 100)
    session.ensure_quad_swapchain(0, 200, 100)
    quad = SimpleNamespace(swapchain_index=0, orientation=(0, 0, 0, 1), position=(0, 0, 0),
                           size=(1, 1))
    swapchains_while_both_live = runtime.kinds_living().count("Swapchain")

    for _frame in range(RETIRE_AFTER_FRAMES):
        layers = _the_layers_it_ends_a_frame_with(
            session, runtime, [xr.View()], project=False, quads=[quad])

    assert layers == 1
    assert runtime.kinds_living().count("Swapchain") == swapchains_while_both_live - 1


def test_a_frame_the_runtime_will_not_show_locates_no_views():
    session, _runtime, _scene = _a_session_drawing(should_render=False)

    assert session.frame_begin() == (False, 5, [])


def test_a_frame_the_headset_is_not_showing_yet_locates_no_views():
    session, _runtime, _scene = _a_session()

    assert session.frame_begin() == (False, 5, [])


def test_a_frame_the_runtime_shows_draws_the_views_it_located():
    session, _runtime, _scene = _a_session_drawing()

    should_render, display_time, views = session.frame_begin()

    assert (should_render, display_time, len(views)) == (True, 5, 2)
    assert session.views_tracked is True


def test_each_eye_is_drawn_into_the_image_the_runtime_handed_over_then_given_back():
    session, runtime, scene = _a_session_drawing()
    eye = session.swapchains[1]

    session.bind_eye_framebuffer(1)
    session.release_eye_framebuffer(1)

    assert scene.drawn_into == [(eye.images[1], *EYE_SIZE)]
    assert runtime.journal[-2:] == [
        "done drawing", f"release an image of {address_of(eye.handle):#x}"]


def test_a_quad_is_drawn_into_its_own_swapchains_image():
    session, runtime, scene = _a_session_drawing()
    session.ensure_quad_swapchain(0, 200, 100)
    quad = session._quads[0]

    session.bind_quad_framebuffer(0)
    session.release_quad_framebuffer(0)

    assert scene.drawn_into == [(quad.images[1], 200, 100)]
    assert runtime.journal[-1] == f"release an image of {address_of(quad.handle):#x}"


def test_the_scene_windows_close_box_is_heard():
    session, _runtime, scene = _a_session()
    scene.asked_to_close = True

    assert session.window_close_requested() is True



def test_the_display_period_is_learned_from_the_first_frame_the_runtime_paces():
    session, _runtime, _scene = _a_session_drawing()
    before = session.display_period_ms

    session.frame_begin()

    assert (before, round(session.display_period_ms, 3)) == (None, 11.111)
