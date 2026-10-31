"""The headset process: the session's three Funestras composited into one
OpenXR scene.

The desktop session runs the Main Funestra and the two satellites as processes
owning windows; an OpenXR runtime gives the headset to one rendering process, so
every Funestra here is a surface of this one.  What hangs in the room and how the
controllers move it is :mod:`fun_time_vr.funestras`, tested off the headset; this
is the shell around it, which needs one: the OpenXR session, the eye passes and
the frame loop, which only copies each newest frame into the scene and hands the
compositor its layers.  Both ends of a session are covered from here
(:mod:`fun_time_vr.cover`), the headset having no monitors for the desktop's
overlay windows.
"""
from __future__ import annotations

import argparse
import logging
import math
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from app_support.logging_utils import install_exception_logging
from app_support.win32 import set_app_user_model_id

from fun_time import preview_marker
from fun_time.event_log import NOTICE, EventLogHandler, event_log_path
from fun_time.manifest import LaunchManifest
from fun_time.project_paths import PROJECT_VR_ICON
from fun_time.session_handoff import (
    headset_hold_asked,
    headset_hold_stops_the_runtime,
    report_the_headset_held,
)

from . import vr_runtime
from .bringup import open_vr_session
from .cover import COVER_CLEAR
from .funestras import (
    Arranged,
    Arrangement,
    CoverUnit,
    TheRoom,
    VrSettings,
    draw_eyes,
    hang_the_room,
    orientation_of,
)
from .matrices import (
    fov_to_projection_matrix,
    pose_to_view_matrix,
    yaw_of_orientation,
    yaw_rotation_matrix,
)
from .perf import FramePerf
from .render import SceneRenderer
from .scheduling import ahead_of_background_work

logger = logging.getLogger(__name__)


def _show_error_popup(message: str) -> None:
    """Say why the headset never lit up -- a hidden launch that just exits
    is indistinguishable from a crash."""
    # Qt loads only for this: a headset session draws through OpenXR.
    from shared_ui.alert import show_alert  # noqa: PLC0415

    show_alert("FunTimeVR", message,
               icon=preview_marker.icon_file(PROJECT_VR_ICON, preview_marker.shown_as()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The FunTimeVR headset process (one OpenXR scene, three Funestras)")
    parser.add_argument("--manifest", type=Path, required=True,
                        help="The windows bridge manifest INI the orchestrator wrote")
    return parser


def set_up_logging() -> logging.Logger:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    install_exception_logging(logger)
    return logger


def main(argv: list[str] | None = None) -> int:
    set_up_logging()
    args = build_parser().parse_args(argv)
    manifest = LaunchManifest.read(args.manifest)
    # The strip that shows this process's notices tails the session's event log,
    # which is appended to per line so several processes can share it.
    _log_into_the_event_log(Path(manifest.commands.dashboard_cmd_file).parent)
    vr = VrSettings.read(args.manifest)
    # Before any window exists: one app, one pinned button (win32_taskbar).
    try:
        set_app_user_model_id(preview_marker.session_identity(preview_marker.shown_as()))
    except OSError:
        logger.debug("Could not claim the taskbar identity", exc_info=True)

    ready = vr_runtime.ensure_ready()
    if ready.readiness is not vr_runtime.Readiness.READY:
        logger.error("VR not available: %s", ready.readiness.value)
        _show_error_popup(vr_runtime.explain(ready))
        return 1
    with ahead_of_background_work():
        return _run(manifest, vr, args.manifest)


def _log_into_the_event_log(state_dir: Path) -> None:
    handler = EventLogHandler(event_log_path(state_dir))
    handler.setLevel(NOTICE)  # announcements only; the log file carries the rest
    logging.getLogger("fun_time_vr").addHandler(handler)


def _update_quad_layer(
    session, renderer: SceneRenderer, index: int, unit,
    scene_yaw_deg: float, scene_pitch_deg: float,
):
    """Refresh *unit*'s quad swapchain if its texture moved, and describe the
    layer to submit — or None before the first frame of content exists."""
    from .vr_session import QuadLayer  # noqa: PLC0415 (sibling of the lazy VRSession below)

    if unit.layer_dirty:
        session.ensure_quad_swapchain(index, unit.target.width, unit.target.height)
        session.bind_quad_framebuffer(index)
        renderer.copy_texture(unit.target.texture)
        session.release_quad_framebuffer(index)
        unit.layer_dirty = False
        unit.layer_rect = (unit.target.width, unit.target.height)
    if unit.layer_rect is None:
        return None
    position, orientation, size = unit.layer_placement(scene_yaw_deg, scene_pitch_deg)
    return QuadLayer(
        swapchain_index=index, position=position, orientation=orientation, size=size,
    )


def _draw_cover(session, renderer: SceneRenderer, cover: CoverUnit, views) -> None:
    """Fill both eyes with the cover: ground edge to edge, panel at the heading
    he is facing, latched once he is at the lenses (:class:`CoverAnchor`), through
    the rotation-only view matrix the scene uses so it holds still as he turns."""
    was_held = cover.anchor.held
    heading = cover.anchor.heading(
        yaw_of_orientation(orientation_of(views[0])), settled=cover.anchor_settled)
    if was_held is None and cover.anchor.held is not None:
        logger.info("Cover placed at heading %.0f°", math.degrees(heading))
    hanging = yaw_rotation_matrix(heading)
    for eye_index, view in enumerate(views):
        session.bind_eye_framebuffer(eye_index)
        renderer.begin_eye(COVER_CLEAR)
        if cover.screen.ready:
            projection = fov_to_projection_matrix(
                view.fov.angle_left, view.fov.angle_right,
                view.fov.angle_up, view.fov.angle_down,
                0.05, 100.0,
            )
            view_matrix = pose_to_view_matrix((0.0, 0.0, 0.0), orientation_of(view))
            renderer.draw_screen(
                cover.screen.mesh, cover.texture.texture,
                np.ascontiguousarray(projection @ view_matrix @ hanging, dtype=np.float32),
            )
        session.release_eye_framebuffer(eye_index)


TEARDOWN_COVER_TIMEOUT_S = 1.0  # a couple of frames; past this, not worth it

FIRST_COVER_TIMEOUT_S = 5.0  # the READY event, a frame or two after the session


def _present_the_cover(session, renderer: SceneRenderer, cover: CoverUnit) -> bool:
    """One frame of cover; whether it reached the eyes.  Never at the cost of
    the launch or quit it runs inside."""
    try:
        should_render, display_time, views = session.frame_begin()
        cover.render_latest_frame()
        covered = bool(should_render and views and cover.showing)
        if covered:
            _draw_cover(session, renderer, cover, views)
        session.frame_end(display_time, views, project=covered)
        return covered
    except Exception:
        logger.debug("Could not put the cover up", exc_info=True)
        return False


def _raise_the_cover(session, renderer: SceneRenderer, cover: CoverUnit) -> None:
    """Get the cover in front of the eyes BEFORE the Funestras are built: built
    first and shown after, it was up for the tail of a launch that had already
    finished.  One frame here carries through the mpv bring-up."""
    if not cover.wanted:
        return  # a Funestra with no orchestrator: nothing is coming to wait for
    deadline = time.monotonic() + FIRST_COVER_TIMEOUT_S
    while session.running and time.monotonic() < deadline:
        session.poll_events()
        if session.session_ready and _present_the_cover(session, renderer, cover):
            return
        time.sleep(0.005)


# Past this no session is coming (docs/entering-vr.md).
HEADSET_HOLD_TIMEOUT_S = 180.0


def _hold_the_headset(
    session, renderer: SceneRenderer, cover: CoverUnit, state_dir: Path,
) -> None:
    """Cover the headset until the next session releases it."""
    report_the_headset_held(state_dir)
    deadline = time.monotonic() + HEADSET_HOLD_TIMEOUT_S
    try:
        while session.running and time.monotonic() < deadline:
            if not headset_hold_asked(state_dir):
                return
            session.poll_events()
            if not session.session_ready:
                return  # nothing is being presented; there is nothing to hold
            cover.refresh()
            _present_the_cover(session, renderer, cover)
    except Exception:
        logger.debug("Could not hold the headset's cover", exc_info=True)


def _cover_the_teardown(session, renderer: SceneRenderer, cover: CoverUnit) -> None:
    """Raise the closing cover and hold it while this process comes apart.  The
    ordinary quit asks through the shutdown progress file; this is the other way
    out — window closed, interrupt, a runtime that ended the session."""
    cover.closing_now()
    try:
        cover.refresh()
        deadline = time.monotonic() + TEARDOWN_COVER_TIMEOUT_S
        while session.running and time.monotonic() < deadline:
            session.poll_events()
            if not session.session_ready:
                break  # nothing is being presented; there is nothing to cover
            if _present_the_cover(session, renderer, cover):
                break
    except Exception:
        # A compositor that has let go of us must not turn a quit into a crash.
        logger.debug("Could not raise the closing cover", exc_info=True)
    cover.settled()  # however that went, teardown has waited long enough


def _run(manifest: LaunchManifest, vr: VrSettings, manifest_path: Path) -> int:
    # The GL/XR stack loads only after the runtime probe found a headset.
    import glfw  # noqa: PLC0415
    import xr  # noqa: PLC0415

    from .vr_session import VRSession  # noqa: PLC0415

    opened = open_vr_session(
        VRSession, device_not_ready=xr.exception.GraphicsDeviceInvalidError)
    if opened.session is None:
        _show_error_popup(opened.message)
        return 1
    session = opened.session
    renderer = SceneRenderer()
    perf = FramePerf(logger=logger)
    state_dir = Path(manifest.commands.dashboard_cmd_file).parent
    # Before the Funestras and refreshed between them: each opens media.
    cover = CoverUnit(state_dir)
    _raise_the_cover(session, renderer, cover)
    the_room = hang_the_room(
        manifest, vr, session.shared_contexts(), manifest_path=manifest_path, cover=cover,
        perf=perf, present_the_cover=lambda: _present_the_cover(session, renderer, cover))
    workers = the_room.start_pumping(perf)
    logger.info(
        "Entering the VR loop (three Funestras up, compositor layers %s)",
        "on" if vr.compositor_layers else "off",
    )
    try:
        _show_the_room(session, renderer, the_room, perf, glfw, use_layers=vr.compositor_layers)
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        _tear_down(session, renderer, the_room, workers, state_dir)
    return 0


def _show_the_room(session, renderer: SceneRenderer, the_room: TheRoom, perf: FramePerf,
                   glfw, *, use_layers: bool) -> None:
    arranging = Arrangement(the_room)
    cover = the_room.cover
    last_frame_time = time.monotonic()
    while session.running and not the_room.stop.is_set():
        session.poll_events()
        the_room.note_the_wearer(session.focused, now=time.monotonic())
        if session.window_close_requested():
            break
        if cover.holding:
            break  # the session is crossing over; the teardown holds it up
        if not session.session_ready:
            # The pump thread keeps every channel live while the headset
            # warms up, so the orchestrator sees status the moment it asks.
            cover.settled()  # nothing can be shown from here, cover included
            glfw.poll_events()
            time.sleep(0.01)
            continue
        now = time.monotonic()
        _frame(session, renderer, the_room, arranging, perf,
               elapsed_s=now - last_frame_time, now=now, use_layers=use_layers)
        last_frame_time = now
        glfw.poll_events()


def _frame(session, renderer: SceneRenderer, the_room: TheRoom, arranging: Arrangement,
           perf: FramePerf, *, elapsed_s: float, now: float, use_layers: bool) -> None:
    cover = the_room.cover
    t0 = time.perf_counter()
    should_render, display_time, views = session.frame_begin()
    t1 = time.perf_counter()
    the_room.render_latest_frames()
    the_room.cover_watch.frame_began(
        blank=the_room.still_blank(), focused=session.focused,
        views_tracked=session.views_tracked)
    t2 = time.perf_counter()
    quads: list = []
    project = covered = False
    t3 = t2
    if should_render and views and cover.showing:
        # No quads: the runtime composites those OVER our layer, so a
        # screen submitted as one shows through the cover.
        covered = project = True
        the_room.cover_watch.drawing(now)
        t3 = time.perf_counter()
        _draw_cover(session, renderer, cover, views)
    elif should_render and views:
        if session.focused:
            the_room.route_audio()
        session.sync_controller(display_time)
        arranged = arranging.arrange(session.hands, views, elapsed_s=elapsed_s, now=now)
        quads, as_quads = (_quad_layers(session, renderer, the_room, arranged) if use_layers
                           else ([], set()))
        project = True  # the panel lives in the projection layer
        t3 = time.perf_counter()
        draw_eyes(session, renderer, arranged.hangings, the_room.pointing, views,
                   arranged.scene_rotation, screens=arranged.screens, as_quads=as_quads)
    t4 = time.perf_counter()
    session.frame_end(display_time, views, project=project, quads=quads)
    t5 = time.perf_counter()
    the_room.cover_watch.frame_ended(
        covered=covered, focused=session.focused, should_render=should_render, now=now)
    perf.note("wait", (t1 - t0) * 1e3)
    perf.note("frames", (t2 - t1) * 1e3)
    perf.note("layers", (t3 - t2) * 1e3)
    perf.note("eyes", (t4 - t3) * 1e3)
    perf.note("end", (t5 - t4) * 1e3)
    perf.frame_done()


def _quad_layers(session, renderer: SceneRenderer, the_room: TheRoom,
                 arranged: Arranged) -> tuple[list, set[str]]:
    """The mpv-backed screens as quads, out of the scene as each is taken."""
    quads: list = []
    as_quads: set[str] = set()
    main_unit = the_room.main_unit
    for index, unit in enumerate(the_room.videos):
        if unit is main_unit and (main_unit.wraps_the_viewer or not main_unit.owns_the_slot):
            continue
        quad = _update_quad_layer(
            session, renderer, index, unit, math.degrees(arranged.scene_yaw),
            arranged.scene_pitch_deg)
        if quad is not None:
            quads.append(quad)
            as_quads.add(unit.screen_name)
    return quads, as_quads


def _tear_down(session, renderer: SceneRenderer, the_room: TheRoom,
               workers: Sequence[threading.Thread], state_dir: Path) -> None:
    _cover_the_teardown(session, renderer, the_room.cover)
    the_room.stop.set()
    # Only to settle the file channels -- funestra.mpv_gate makes the closes safe.
    for worker in workers:
        worker.join(timeout=2.0)
    held = headset_hold_asked(state_dir)
    stop_runtime = held and headset_hold_stops_the_runtime(state_dir)  # while it is there
    the_room.close(keep_the_cover=held)
    if held:
        _hold_the_headset(session, renderer, the_room.cover, state_dir)
        the_room.cover.close()
    the_room.pointing.close()
    renderer.close()
    session.close()
    logger.info("Shutdown complete")
    if stop_runtime:  # the orchestrator that knew this exited under the hold
        vr_runtime.stop_the_runtime_a_session_started(state_dir)


if __name__ == "__main__":
    raise SystemExit(main())
