from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from fun_time.session_handoff import (
    headset_hold_asked,
    headset_hold_stops_the_runtime,
    report_the_headset_held,
)

from . import room, vr_runtime
from .funestras import (
    Arranged,
    Arrangement,
    LoadingScreenUnit,
    TheRoom,
    orientation_of,
)
from .loading_screen import LOADING_SCREEN_CLEAR
from .matrices import (
    fov_to_projection_matrix,
    pose_to_view_matrix,
    yaw_of_orientation,
    yaw_rotation_matrix,
)
from .perf import FramePerf
from .render import SceneRenderer

logger = logging.getLogger(__name__)

TEARDOWN_LOADING_SCREEN_TIMEOUT_S = 1.0

FIRST_LOADING_SCREEN_TIMEOUT_S = 5.0

HEADSET_HOLD_TIMEOUT_S = 180.0

Clock = Callable[[], float]
Sleep = Callable[[float], None]


class Headset(NamedTuple):
    session: Any
    renderer: SceneRenderer
    perf: FramePerf


@dataclass(frozen=True)
class QuadLayer:
    swapchain_index: int
    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    size: tuple[float, float]


def refresh_quad_layer(headset: Headset, index: int, unit, arranged: Arranged) -> QuadLayer | None:
    if unit.layer_dirty:
        headset.session.ensure_quad_swapchain(index, unit.target.width, unit.target.height)
        headset.session.bind_quad_framebuffer(index)
        headset.renderer.copy_texture(unit.target.texture)
        headset.session.release_quad_framebuffer(index)
        unit.layer_dirty = False
        unit.layer_rect = (unit.target.width, unit.target.height)
    if unit.layer_rect is None:
        return None
    position, orientation, size = unit.layer_placement(
        math.degrees(arranged.scene_yaw), arranged.scene_pitch_deg)
    return QuadLayer(
        swapchain_index=index, position=position, orientation=orientation, size=size,
    )


def _view_from_the_spheres_center(view) -> np.ndarray:
    return pose_to_view_matrix((0.0, 0.0, 0.0), orientation_of(view))


def draw_eyes(
    headset: Headset,
    arranged: Arranged,
    pointing,
    views,
    *,
    as_quads: set[str],
) -> None:
    session, renderer = headset.session, headset.renderer
    scene_rotation = arranged.scene_rotation
    wrap = room.wrapped(arranged.hangings)
    flat = room.drawn(arranged.hangings, screens=arranged.screens, as_quads=as_quads)
    for eye_index, view in enumerate(views):
        session.bind_eye_framebuffer(eye_index)
        renderer.begin_eye()
        projection_matrix = fov_to_projection_matrix(view.fov, 0.05, 100.0)
        view_proj = projection_matrix @ _view_from_the_spheres_center(view) @ scene_rotation
        view_proj32 = np.ascontiguousarray(view_proj, dtype=np.float32)
        if wrap is not None:
            inv32 = np.ascontiguousarray(np.linalg.inv(view_proj), dtype=np.float32)
            renderer.draw_immersive(wrap.wrap, wrap.frame.texture, inv32, eye_index)
        for mesh, texture, blend in flat:
            renderer.draw_screen(mesh, texture, view_proj32, blend=blend)
        pointing.draw(renderer, view_proj32)
        session.release_eye_framebuffer(eye_index)


def draw_loading_screen(headset: Headset, loading_screen: LoadingScreenUnit, views) -> None:
    was_held = loading_screen.anchor.held
    heading = loading_screen.anchor.heading(
        yaw_of_orientation(orientation_of(views[0])), settled=loading_screen.anchor_settled)
    if was_held is None and loading_screen.anchor.held is not None:
        logger.info("Loading screen placed at heading %.0f°", math.degrees(heading))
    hanging = yaw_rotation_matrix(heading)
    for eye_index, view in enumerate(views):
        headset.session.bind_eye_framebuffer(eye_index)
        headset.renderer.begin_eye(LOADING_SCREEN_CLEAR)
        if loading_screen.screen.ready:
            projection = fov_to_projection_matrix(view.fov, 0.05, 100.0)
            headset.renderer.draw_screen(
                loading_screen.screen.mesh, loading_screen.texture.texture,
                np.ascontiguousarray(
                    projection @ _view_from_the_spheres_center(view) @ hanging, dtype=np.float32),
            )
        headset.session.release_eye_framebuffer(eye_index)


def present_the_loading_screen(headset: Headset, loading_screen: LoadingScreenUnit) -> bool:
    try:
        should_render, display_time, views = headset.session.frame_begin()
        loading_screen.render_latest_frame()
        covered = bool(should_render and views and loading_screen.showing)
        if covered:
            draw_loading_screen(headset, loading_screen, views)
        headset.session.frame_end(display_time, views, project=covered)
        return covered
    except Exception:
        logger.debug("Could not put the loading screen up", exc_info=True)
        return False


def raise_the_loading_screen(
    headset: Headset, loading_screen: LoadingScreenUnit, *, clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
) -> None:
    if not loading_screen.wanted:
        return
    deadline = clock() + FIRST_LOADING_SCREEN_TIMEOUT_S
    while headset.session.running and clock() < deadline:
        headset.session.poll_events()
        if headset.session.session_ready and present_the_loading_screen(headset, loading_screen):
            return
        sleep(0.005)


def hold_the_headset(
    headset: Headset, loading_screen: LoadingScreenUnit, state_dir: Path, *, clock: Clock = time.monotonic,
) -> None:
    report_the_headset_held(state_dir)
    deadline = clock() + HEADSET_HOLD_TIMEOUT_S
    try:
        while headset.session.running and clock() < deadline:
            if not headset_hold_asked(state_dir):
                return
            headset.session.poll_events()
            if not headset.session.session_ready:
                return
            loading_screen.refresh()
            present_the_loading_screen(headset, loading_screen)
    except Exception:
        logger.debug("Could not hold the headset's loading screen", exc_info=True)


def closing_screen_over_the_teardown(headset: Headset, loading_screen: LoadingScreenUnit, *, clock: Clock = time.monotonic) -> None:
    loading_screen.closing_now()
    try:
        loading_screen.refresh()
        deadline = clock() + TEARDOWN_LOADING_SCREEN_TIMEOUT_S
        while headset.session.running and clock() < deadline:
            headset.session.poll_events()
            if not headset.session.session_ready:
                break
            if present_the_loading_screen(headset, loading_screen):
                break
    except Exception:
        logger.debug("Could not raise the closing screen", exc_info=True)
    loading_screen.settled()


class FrameLoop:
    def __init__(self, headset: Headset, the_room: TheRoom, *, use_layers: bool,
                 clock: Clock = time.monotonic, sleep: Sleep = time.sleep) -> None:
        self._headset = headset
        self._room = the_room
        self._use_layers = use_layers
        self._clock = clock
        self._sleep = sleep
        self.arranging = Arrangement(the_room)

    def show_the_room(self, glfw) -> None:
        session, the_room, clock = self._headset.session, self._room, self._clock
        loading_screen = the_room.loading_screen
        last_frame_time = clock()
        while session.running and not the_room.stop.is_set():
            session.poll_events()
            the_room.note_the_wearer(session.focused, now=clock())
            if session.window_close_requested():
                break
            if loading_screen.holding:
                break
            if not session.session_ready:
                loading_screen.settled()
                glfw.poll_events()
                self._sleep(0.01)
                continue
            now = clock()
            self.show_a_frame(elapsed_s=now - last_frame_time, now=now)
            last_frame_time = now
            glfw.poll_events()

    def show_a_frame(self, *, elapsed_s: float, now: float) -> None:
        headset, the_room = self._headset, self._room
        session, perf = headset.session, headset.perf
        loading_screen = the_room.loading_screen
        t0 = time.perf_counter()
        should_render, display_time, views = session.frame_begin()
        t1 = time.perf_counter()
        the_room.render_latest_frames()
        the_room.loading_screen_watch.frame_began(
            blank=the_room.still_blank(), focused=session.focused,
            views_tracked=session.views_tracked)
        t2 = time.perf_counter()
        quads: list[QuadLayer] = []
        project = covered = False
        t3 = t2
        if should_render and views and loading_screen.showing:
            covered = project = True
            the_room.loading_screen_watch.drawing(now)
            t3 = time.perf_counter()
            draw_loading_screen(headset, loading_screen, views)
        elif should_render and views:
            if session.focused:
                the_room.route_audio()
            session.sync_controller(display_time)
            arranged = self.arranging.arrange(session.hands, views, elapsed_s=elapsed_s, now=now)
            quads, as_quads = self.quads_the_runtime_holds_steady(arranged) if self._use_layers else ([], set())
            project = True
            t3 = time.perf_counter()
            draw_eyes(headset, arranged, the_room.pointing, views, as_quads=as_quads)
        t4 = time.perf_counter()
        session.frame_end(display_time, views, project=project, quads=quads)
        t5 = time.perf_counter()
        the_room.loading_screen_watch.frame_ended(
            covered=covered, focused=session.focused, should_render=should_render, now=now)
        perf.note("wait", (t1 - t0) * 1e3)
        perf.note("frames", (t2 - t1) * 1e3)
        perf.note("layers", (t3 - t2) * 1e3)
        perf.note("eyes", (t4 - t3) * 1e3)
        perf.note("end", (t5 - t4) * 1e3)
        perf.frame_done()

    def quads_the_runtime_holds_steady(self, arranged: Arranged) -> tuple[list[QuadLayer], set[str]]:
        quads: list[QuadLayer] = []
        as_quads: set[str] = set()
        main_unit = self._room.main_unit
        for index, unit in enumerate(self._room.videos):
            if unit is main_unit and (main_unit.wraps_the_viewer or not main_unit.owns_the_slot):
                continue
            quad = refresh_quad_layer(self._headset, index, unit, arranged)
            if quad is not None:
                quads.append(quad)
                as_quads.add(unit.screen_name)
        return quads, as_quads


def tear_down(headset: Headset, the_room: TheRoom, state_dir: Path, *,
              clock: Clock = time.monotonic,
              stop_the_runtime: Callable[[Path], None] = vr_runtime.stop_the_runtime_a_session_started,
              ) -> None:
    closing_screen_over_the_teardown(headset, the_room.loading_screen, clock=clock)
    the_room.stop.set()
    for worker in the_room.workers:
        worker.join(timeout=2.0)
    held = headset_hold_asked(state_dir)
    stop_runtime = held and headset_hold_stops_the_runtime(state_dir)
    the_room.close(keep_the_loading_screen=held)
    if held:
        hold_the_headset(headset, the_room.loading_screen, state_dir, clock=clock)
        the_room.loading_screen.close()
    the_room.pointing.close()
    headset.renderer.close()
    headset.session.close()
    logger.info("Shutdown complete")
    if stop_runtime:
        stop_the_runtime(state_dir)
