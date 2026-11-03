from __future__ import annotations

import logging
import math
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from fun_time.session_handoff import headset_is_held, release_the_headset
from fun_time.session_handoff import hold_the_headset as ask_for_the_hold
from fun_time_vr.frame_loop import (
    FIRST_LOADING_SCREEN_TIMEOUT_S,
    HEADSET_HOLD_TIMEOUT_S,
    TEARDOWN_LOADING_SCREEN_TIMEOUT_S,
    FrameLoop,
    Headset,
    QuadLayer,
    closing_screen_over_the_teardown,
    hold_the_headset,
    present_the_loading_screen,
    raise_the_loading_screen,
    refresh_quad_layer,
    tear_down,
)
from fun_time_vr.funestras import Arranged
from fun_time_vr.loading_screen import LOADING_SCREEN_CLEAR


def _a_view(*, yaw_deg: float = 0.0):
    half = math.radians(yaw_deg) / 2
    return SimpleNamespace(
        pose=SimpleNamespace(
            position=SimpleNamespace(x=0.0, y=1.6, z=0.0),
            orientation=SimpleNamespace(x=0.0, y=math.sin(half), z=0.0, w=math.cos(half))),
        fov=SimpleNamespace(angle_left=-0.8, angle_right=0.8, angle_up=0.7, angle_down=-0.7))


_BOTH_EYES = [_a_view(), _a_view()]
_UNARRANGED = Arranged([], [], np.eye(4, dtype=np.float32), 0.0, 0.0)


class _ASession:
    """What the frame loop asks of the OpenXR session, answered from canned
    frames and written down."""

    def __init__(self, *frames, ready=True, running=True, focused=True):
        self.frames = list(frames)
        self.session_ready = ready
        self.running = running
        self.focused = focused
        self.views_tracked = True
        self.hands = {}
        self.close_requested = False
        self.polled = 0
        self.synced: list[int] = []
        self.ended: list[tuple[int, bool, list]] = []
        self.framebuffers: list[tuple] = []
        self.closed = False

    def poll_events(self) -> None:
        self.polled += 1

    def window_close_requested(self) -> bool:
        return self.close_requested

    def frame_begin(self):
        return self.frames.pop(0) if self.frames else (False, 0, [])

    def frame_end(self, display_time, views, *, project=True, quads=None) -> None:
        self.ended.append((display_time, project, list(quads or [])))

    def sync_controller(self, display_time) -> None:
        self.synced.append(display_time)

    def bind_eye_framebuffer(self, eye_index) -> None:
        self.framebuffers.append(("eye", eye_index))

    def release_eye_framebuffer(self, eye_index) -> None:
        pass

    def ensure_quad_swapchain(self, index, width, height) -> None:
        self.framebuffers.append(("quad", index, width, height))

    def bind_quad_framebuffer(self, index) -> None:
        pass

    def release_quad_framebuffer(self, index) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _ARenderer:
    def __init__(self) -> None:
        self.begun: list[tuple] = []
        self.screens: list[tuple] = []
        self.copied: list[int] = []
        self.closed = False

    def begin_eye(self, clear=(0.0, 0.0, 0.0, 1.0)) -> None:
        self.begun.append(clear)

    def draw_screen(self, mesh, texture, view_proj, *, blend=False) -> None:
        self.screens.append((mesh, texture))

    def copy_texture(self, texture) -> None:
        self.copied.append(texture)

    def close(self) -> None:
        self.closed = True


class _APerf:
    def __init__(self) -> None:
        self.stages: list[str] = []
        self.frames = 0

    def note(self, stage, ms) -> None:
        self.stages.append(stage)

    def frame_done(self) -> None:
        self.frames += 1


def _a_headset(session=None, renderer=None) -> Headset:
    return Headset(session or _ASession(), renderer or _ARenderer(), _APerf())


class _ALoadingScreen:
    def __init__(self, *, showing=True, wanted=True, screen_ready=True):
        self.showing = showing
        self.wanted = wanted
        self.holding = False
        self.anchor_settled = False
        self.anchor = SimpleNamespace(held=None, heading=self._heading)
        self.screen = SimpleNamespace(ready=screen_ready, mesh="loading-screen-mesh")
        self.texture = SimpleNamespace(texture=7)
        self.told: list[str] = []

    def _heading(self, yaw, *, settled):
        self.anchor.held = yaw
        return yaw

    def render_latest_frame(self) -> None:
        self.told.append("rendered")

    def refresh(self) -> None:
        self.told.append("refreshed")

    def closing_now(self) -> None:
        self.told.append("closing")

    def settled(self) -> None:
        self.told.append("settled")

    def close(self) -> None:
        self.told.append("closed")


class _AClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


def _a_frame(*, rendering=True, at=5, views=_BOTH_EYES):
    return (rendering, at, list(views) if rendering else [])


class TestPresentingTheLoadingScreen:
    def test_a_frame_the_runtime_will_show_gets_the_loading_screen_in_both_eyes(self):
        session, renderer, loading_screen = _ASession(_a_frame(at=5)), _ARenderer(), _ALoadingScreen()

        assert present_the_loading_screen(_a_headset(session, renderer), loading_screen) is True
        assert renderer.begun == [LOADING_SCREEN_CLEAR, LOADING_SCREEN_CLEAR]
        assert renderer.screens == [("loading-screen-mesh", 7), ("loading-screen-mesh", 7)]
        assert session.ended == [(5, True, [])]

    def test_a_frame_the_runtime_will_not_show_is_ended_with_nothing_in_it(self):
        session, renderer = _ASession(_a_frame(rendering=False, at=5)), _ARenderer()

        assert present_the_loading_screen(_a_headset(session, renderer), _ALoadingScreen()) is False
        assert renderer.begun == []
        assert session.ended == [(5, False, [])]

    def test_a_loading_screen_with_nothing_painted_yet_does_not_reach_the_eyes(self):
        session = _ASession(_a_frame())

        assert present_the_loading_screen(_a_headset(session), _ALoadingScreen(showing=False)) is False
        assert session.ended == [(5, False, [])]

    def test_a_session_that_refuses_the_frame_costs_nothing_but_the_loading_screen(self):
        session = _ASession()
        session.frame_begin = lambda: (_ for _ in ()).throw(RuntimeError("gone"))

        assert present_the_loading_screen(_a_headset(session), _ALoadingScreen()) is False

    def test_the_loading_screen_is_placed_once_at_the_heading_he_faces(self, caplog):
        loading_screen = _ALoadingScreen()
        headset = _a_headset(_ASession(_a_frame(views=[_a_view(yaw_deg=90.0)] * 2), _a_frame()))

        with caplog.at_level(logging.INFO, logger="fun_time_vr.frame_loop"):
            present_the_loading_screen(headset, loading_screen)
            present_the_loading_screen(headset, loading_screen)

        assert [m for m in caplog.messages if m.startswith("Loading screen placed")] == [
            "Loading screen placed at heading 90°"]

    def test_the_ground_fills_the_eyes_before_the_panel_has_been_painted(self):
        renderer = _ARenderer()

        present_the_loading_screen(_a_headset(_ASession(_a_frame()), renderer), _ALoadingScreen(screen_ready=False))

        assert renderer.begun == [LOADING_SCREEN_CLEAR, LOADING_SCREEN_CLEAR]
        assert renderer.screens == []


class TestRaisingTheLoadingScreen:
    def test_a_loading_screen_nobody_is_waiting_for_is_never_raised(self):
        session = _ASession(_a_frame())

        raise_the_loading_screen(_a_headset(session), _ALoadingScreen(wanted=False))

        assert session.polled == 0
        assert session.ended == []

    def test_it_polls_until_the_session_is_ready_and_stops_at_the_first_covered_frame(self):
        clock = _AClock()
        session = _ASession(_a_frame(rendering=False), _a_frame(), _a_frame(), ready=False)
        session.poll_events = lambda: setattr(session, "session_ready", True)

        raise_the_loading_screen(_a_headset(session), _ALoadingScreen(), clock=clock, sleep=clock.sleep)

        assert [project for _at, project, _quads in session.ended] == [False, True]
        assert len(session.frames) == 1

    def test_it_gives_up_once_the_first_frame_has_had_its_time(self):
        clock = _AClock()
        session = _ASession(ready=True)

        raise_the_loading_screen(_a_headset(session), _ALoadingScreen(), clock=clock, sleep=clock.sleep)

        assert clock.t >= FIRST_LOADING_SCREEN_TIMEOUT_S
        assert session.ended, "it never tried"


class TestHoldingTheHeadset:
    def test_it_covers_the_headset_until_the_hold_is_released(self, tmp_path):
        ask_for_the_hold(tmp_path, stop_runtime=False)
        session, loading_screen = _ASession(_a_frame(), _a_frame(), _a_frame()), _ALoadingScreen()
        frames_shown = 0

        def poll():
            nonlocal frames_shown
            frames_shown += 1
            if frames_shown == 3:
                release_the_headset(tmp_path)
        session.poll_events = poll

        hold_the_headset(_a_headset(session), loading_screen, tmp_path, clock=_AClock())

        assert [project for _at, project, _quads in session.ended] == [True, True, True]
        assert loading_screen.told.count("refreshed") == 3

    def test_it_says_the_headset_is_held_before_waiting(self, tmp_path):
        hold_the_headset(_a_headset(_ASession(ready=False)), _ALoadingScreen(), tmp_path, clock=_AClock())

        assert headset_is_held(tmp_path)

    def test_a_session_presenting_nothing_has_nothing_to_hold(self, tmp_path):
        ask_for_the_hold(tmp_path, stop_runtime=False)
        session = _ASession(_a_frame(), ready=False)

        hold_the_headset(_a_headset(session), _ALoadingScreen(), tmp_path, clock=_AClock())

        assert session.ended == []

    def test_a_hold_nobody_takes_up_ends_on_its_own(self, tmp_path):
        ask_for_the_hold(tmp_path, stop_runtime=False)
        clock = _AClock()
        session = _ASession()
        session.poll_events = lambda: clock.sleep(1.0)

        hold_the_headset(_a_headset(session), _ALoadingScreen(), tmp_path, clock=clock)

        assert clock.t >= HEADSET_HOLD_TIMEOUT_S


class TestCoveringTheTeardown:
    def test_the_closing_screen_is_raised_and_held_until_one_frame_reaches_the_eyes(self):
        session, loading_screen = _ASession(_a_frame(rendering=False), _a_frame(), _a_frame()), _ALoadingScreen()

        closing_screen_over_the_teardown(_a_headset(session), loading_screen, clock=_AClock())

        assert loading_screen.told[:2] == ["closing", "refreshed"]
        assert loading_screen.told[-1] == "settled"
        assert [project for _at, project, _quads in session.ended] == [False, True]

    def test_it_settles_once_the_couple_of_frames_are_spent(self):
        clock = _AClock()
        session = _ASession()
        session.poll_events = lambda: clock.sleep(0.5)
        loading_screen = _ALoadingScreen()

        closing_screen_over_the_teardown(_a_headset(session), loading_screen, clock=clock)

        assert clock.t >= TEARDOWN_LOADING_SCREEN_TIMEOUT_S
        assert loading_screen.told[-1] == "settled"

    def test_a_loading_screen_that_cannot_be_read_still_settles(self):
        loading_screen = _ALoadingScreen()
        loading_screen.refresh = lambda: (_ for _ in ()).throw(OSError("gone"))

        closing_screen_over_the_teardown(_a_headset(), loading_screen, clock=_AClock())

        assert loading_screen.told == ["closing", "settled"]


class _ARoom:
    def __init__(self, loading_screen=None, *, main=None, satellites=(), blank=()):
        self.loading_screen = loading_screen or _ALoadingScreen(showing=False)
        self.stop = threading.Event()
        self.workers: list[threading.Thread] = []
        self.main_unit = main or SimpleNamespace(
            screen_name="main", wraps_the_viewer=False, owns_the_slot=True)
        self.satellites = list(satellites)
        self.pointing = SimpleNamespace(draw=lambda renderer, view_proj: None,
                                        close=lambda: self.told.append("pointing closed"))
        self.loading_screen_watch = SimpleNamespace(
            frame_began=lambda **kw: self.told.append(("began", kw)),
            drawing=lambda now: self.told.append(("drawing", now)),
            frame_ended=lambda **kw: self.told.append(("ended", kw)))
        self.told: list = []
        self.wearer: list[bool] = []
        self._blank = tuple(blank)

    @property
    def videos(self):
        return (self.main_unit, *self.satellites)

    def note_the_wearer(self, worn, *, now) -> None:
        self.wearer.append(worn)

    def render_latest_frames(self) -> None:
        self.told.append("frames")

    def still_blank(self):
        return self._blank

    def route_audio(self) -> None:
        self.told.append("audio")

    def close(self, *, keep_the_loading_screen) -> None:
        self.told.append(("closed", keep_the_loading_screen))


class _Arranging:
    def __init__(self, arranged=None):
        self.asked: list = []
        self.arranged = arranged or _UNARRANGED

    def arrange(self, hands, views, *, elapsed_s, now):
        self.asked.append((elapsed_s, now))
        return self.arranged


class _Glfw:
    def __init__(self) -> None:
        self.polled = 0

    def poll_events(self) -> None:
        self.polled += 1


def _a_loop(session, the_room, *, use_layers=False, clock=None) -> FrameLoop:
    clock = clock or _AClock()
    loop = FrameLoop(_a_headset(session), the_room, use_layers=use_layers,
                     clock=clock, sleep=clock.sleep)
    loop.arranging = _Arranging()
    return loop


class TestAFrame:
    def _show(self, session, the_room, *, use_layers=False, now=1.0) -> FrameLoop:
        loop = _a_loop(session, the_room, use_layers=use_layers)
        loop.show_a_frame(elapsed_s=0.1, now=now)
        return loop

    def test_a_showing_loading_screen_is_drawn_in_place_of_the_room_with_no_quads(self):
        session, the_room = _ASession(_a_frame(at=9)), _ARoom(_ALoadingScreen(showing=True))

        loop = self._show(session, the_room, use_layers=True, now=3.0)

        assert loop.arranging.asked == []
        assert session.ended == [(9, True, [])]
        assert ("drawing", 3.0) in the_room.told
        assert the_room.told[-1] == ("ended", {"covered": True, "focused": True,
                                               "should_render": True, "now": 3.0})

    def test_the_room_is_arranged_and_drawn_once_the_loading_screen_is_down(self):
        session, the_room = _ASession(_a_frame(at=9)), _ARoom()

        loop = self._show(session, the_room)

        assert loop.arranging.asked == [(0.1, 1.0)]
        assert session.synced == [9]
        assert session.ended == [(9, True, [])]
        assert session.framebuffers == [("eye", 0), ("eye", 1)]

    def test_audio_follows_the_wearer_only_while_he_is_at_the_lenses(self):
        worn, unworn = _ARoom(), _ARoom()

        self._show(_ASession(_a_frame()), worn)
        self._show(_ASession(_a_frame(), focused=False), unworn)

        assert "audio" in worn.told
        assert "audio" not in unworn.told

    def test_a_frame_the_runtime_will_not_show_still_feeds_the_room_and_the_watch(self):
        session, the_room = _ASession(_a_frame(rendering=False, at=4)), _ARoom(blank=("main",))

        self._show(session, the_room)

        assert session.ended == [(4, False, [])]
        assert the_room.told[0] == "frames"
        assert the_room.told[1] == ("began", {"blank": ("main",), "focused": True,
                                              "views_tracked": True})

    def test_every_stage_of_the_frame_is_timed(self):
        loop = self._show(_ASession(_a_frame()), _ARoom())

        assert loop._headset.perf.stages == ["wait", "frames", "layers", "eyes", "end"]
        assert loop._headset.perf.frames == 1


def _a_video(name, *, dirty=True, rect=None, wraps=False, owns=True):
    return SimpleNamespace(
        screen_name=name, layer_dirty=dirty, layer_rect=rect, wraps_the_viewer=wraps,
        owns_the_slot=owns, target=SimpleNamespace(width=640, height=360, texture=11),
        layer_placement=lambda yaw_deg, pitch_deg: ((0.0, 1.0, -2.0), (0.0, 0.0, 0.0, 1.0),
                                                    (1.6, 0.9)))


class TestQuadLayers:
    def test_a_moved_texture_is_copied_into_its_swapchain_and_described_to_the_compositor(self):
        session, renderer = _ASession(), _ARenderer()
        unit = _a_video("portrait")

        quad = refresh_quad_layer(_a_headset(session, renderer), 1, unit, _UNARRANGED)

        assert quad == QuadLayer(swapchain_index=1, position=(0.0, 1.0, -2.0),
                                 orientation=(0.0, 0.0, 0.0, 1.0), size=(1.6, 0.9))
        assert session.framebuffers == [("quad", 1, 640, 360)]
        assert renderer.copied == [11]
        assert (unit.layer_dirty, unit.layer_rect) == (False, (640, 360))

    def test_a_texture_that_has_not_moved_is_described_without_a_copy(self):
        renderer = _ARenderer()

        quad = refresh_quad_layer(_a_headset(renderer=renderer), 1,
                                  _a_video("portrait", dirty=False, rect=(640, 360)), _UNARRANGED)

        assert quad is not None
        assert renderer.copied == []

    def test_a_screen_with_no_content_yet_is_no_layer(self):
        assert refresh_quad_layer(_a_headset(), 1, _a_video("portrait", dirty=False), _UNARRANGED) is None

    def test_the_satellites_leave_the_scene_as_quads(self):
        the_room = _ARoom(main=_a_video("main"),
                          satellites=[_a_video("portrait"), _a_video("landscape")])

        quads, as_quads = _a_loop(_ASession(), the_room).quad_layers(_UNARRANGED)

        assert [quad.swapchain_index for quad in quads] == [0, 1, 2]
        assert as_quads == {"main", "portrait", "landscape"}

    @pytest.mark.parametrize("main", [_a_video("main", wraps=True), _a_video("main", owns=False)])
    def test_a_main_funestra_wrapping_the_viewer_or_lending_its_slot_stays_in_the_scene(self, main):
        the_room = _ARoom(main=main, satellites=[_a_video("portrait")])

        quads, as_quads = _a_loop(_ASession(), the_room).quad_layers(_UNARRANGED)

        assert [quad.swapchain_index for quad in quads] == [1]
        assert as_quads == {"portrait"}


class TestShowingTheRoom:
    def _show(self, session, the_room, *, clock=None):
        loop = _a_loop(session, the_room, clock=clock)
        glfw = _Glfw()
        loop.show_the_room(glfw)
        return loop, glfw

    def test_it_shows_frames_until_the_room_is_stopped(self):
        the_room = _ARoom()
        session = _ASession(_a_frame(), _a_frame(), _a_frame())
        session.poll_events = lambda: the_room.stop.set() if len(session.ended) == 2 else None

        loop, glfw = self._show(session, the_room)

        assert len(loop.arranging.asked) == 3
        assert glfw.polled == 3

    def test_a_closed_window_ends_it(self):
        session = _ASession(_a_frame())
        session.close_requested = True

        self._show(session, _ARoom())

        assert session.ended == []

    def test_a_loading_screen_held_for_the_next_session_ends_it(self):
        the_room = _ARoom()
        the_room.loading_screen.holding = True

        self._show(_ASession(_a_frame()), the_room)

        assert the_room.told == []

    def test_a_session_not_yet_ready_is_polled_without_a_frame_and_the_loading_screen_settled(self):
        clock = _AClock()
        session = _ASession(_a_frame(), ready=False)
        the_room = _ARoom()
        session.poll_events = lambda: the_room.stop.set() if clock.t >= 0.03 else None

        _loop, glfw = self._show(session, the_room, clock=clock)

        assert session.ended == []
        assert the_room.loading_screen.told == ["settled"] * 4
        assert glfw.polled == 4

    def test_the_room_hears_from_the_headset_whether_anyone_is_wearing_it(self):
        the_room = _ARoom()
        session = _ASession(_a_frame(), focused=False)
        session.poll_events = lambda: the_room.stop.set() if session.ended else None

        self._show(session, the_room)

        assert the_room.wearer == [False, False]


class TestTearingDown:
    def _a_room_with_workers(self, loading_screen=None):
        the_room = _ARoom(loading_screen)
        worker = threading.Thread(target=lambda: None)
        worker.start()
        the_room.workers = [worker]
        return the_room

    def test_the_room_comes_down_under_the_closing_screen(self, tmp_path):
        session, renderer, the_room = _ASession(_a_frame()), _ARenderer(), self._a_room_with_workers(_ALoadingScreen())

        tear_down(_a_headset(session, renderer), the_room, tmp_path, clock=_AClock())

        assert the_room.loading_screen.told[0] == "closing"
        assert the_room.stop.is_set()
        assert not the_room.workers[0].is_alive()
        assert the_room.told == [("closed", False), "pointing closed"]
        assert renderer.closed
        assert session.closed

    def test_a_held_headset_keeps_its_loading_screen_up_until_the_next_session_takes_it(self, tmp_path):
        ask_for_the_hold(tmp_path, stop_runtime=False)
        session, the_room = _ASession(_a_frame(), _a_frame()), _ARoom(_ALoadingScreen())
        polls = []
        session.poll_events = lambda: (
            polls.append(1), release_the_headset(tmp_path) if len(polls) == 2 else None)
        stopped = []

        tear_down(_a_headset(session), the_room, tmp_path, clock=_AClock(),
                  stop_the_runtime=stopped.append)

        assert the_room.told[0] == ("closed", True)
        assert [project for _at, project, _quads in session.ended] == [True, True]
        assert the_room.loading_screen.told[-1] == "closed"
        assert stopped == []

    def test_a_hold_that_ends_the_runtime_ends_it_once_everything_is_closed(self, tmp_path):
        ask_for_the_hold(tmp_path, stop_runtime=True)
        session = _ASession(ready=False)
        stopped = []

        tear_down(_a_headset(session), _ARoom(_ALoadingScreen()), tmp_path, clock=_AClock(),
                  stop_the_runtime=stopped.append)

        assert stopped == [tmp_path]
        assert session.closed
