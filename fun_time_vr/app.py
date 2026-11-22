# An OpenXR runtime gives the headset to one rendering process, so every Funestra here is a surface
# of this one.
from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from app_support.win32 import set_app_user_model_id

from fun_time import preview_marker
from fun_time.console_logging import log_to_the_console
from fun_time.event_log import MESSAGE, event_log_path, queued_event_log
from fun_time.manifest import LaunchManifest
from fun_time.project_paths import PROJECT_VR_ICON
from fun_time.unlogged_messages import wake_the_dashboard

from . import vr_runtime
from .bringup import Bringup, open_vr_session
from .frame_loop import (
    FrameLoop,
    Headset,
    present_the_loading_screen,
    raise_the_loading_screen,
    tear_down,
)
from .funestras import LoadingScreenUnit, VrSettings, hang_the_room
from .perf import FramePerf
from .render import SceneRenderer
from .room import RoomLaunch
from .scheduling import ahead_of_background_work

logger = logging.getLogger(__name__)


def _say_why_the_headset_never_lit_up(message: str) -> None:
    # Qt loads only for this: a headset session draws through OpenXR.
    from shared_ui.alert import show_error  # noqa: PLC0415

    show_error(vr_runtime.APP_NAME, message,
               icon=preview_marker.icon_file(PROJECT_VR_ICON, preview_marker.shown_as()))


def _open_the_session_on_the_headset() -> Bringup:
    import xr  # noqa: PLC0415

    from .vr_session import VRSession  # noqa: PLC0415

    said_ready_before_its_graphics_device_was_up = xr.exception.GraphicsDeviceInvalidError
    return open_vr_session(
        VRSession, device_not_ready=said_ready_before_its_graphics_device_was_up)


def _the_scene_windows_events():
    import glfw  # noqa: PLC0415

    return glfw


@dataclass(frozen=True)
class WhatTheHeadsetReaches:
    claim_identity: Callable[[str], None] = set_app_user_model_id
    ensure_ready: Callable[[], vr_runtime.Probe] = vr_runtime.ensure_ready
    say_why: Callable[[str], None] = _say_why_the_headset_never_lit_up
    ahead_of_background_work: Callable[[], AbstractContextManager[None]] = ahead_of_background_work
    open_session: Callable[[], Bringup] = _open_the_session_on_the_headset
    renderer: Callable[[], Any] = SceneRenderer
    loading_screen: Callable[[Path], LoadingScreenUnit] = LoadingScreenUnit
    hang_the_room: Callable[..., Any] = hang_the_room
    window_events: Callable[[], Any] = _the_scene_windows_events


THE_REAL_HEADSET = WhatTheHeadsetReaches()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="The Fun Time VR headset process (one OpenXR scene, three Funestras)")
    parser.add_argument("--manifest", type=Path, required=True,
                        help="The windows bridge manifest INI the orchestrator wrote")
    return parser


def set_up_logging() -> logging.Logger:
    return log_to_the_console(logger)


def main(argv: list[str] | None = None, *,
         reaches: WhatTheHeadsetReaches = THE_REAL_HEADSET) -> int:
    set_up_logging()
    args = build_parser().parse_args(argv)
    manifest = LaunchManifest.read(args.manifest)
    _announce_into_the_event_log(Path(manifest.commands.dashboard_cmd_file).parent)
    vr = VrSettings.read(args.manifest)
    try:
        reaches.claim_identity(preview_marker.session_identity(preview_marker.shown_as()))
    except OSError:
        logger.debug("Could not claim the taskbar identity", exc_info=True)

    ready = reaches.ensure_ready()
    if ready.readiness is not vr_runtime.Readiness.READY:
        logger.error("VR not available: %s", ready.readiness.value)
        reaches.say_why(vr_runtime.explain(ready))
        return 1
    with reaches.ahead_of_background_work():
        return _run(manifest, vr, reaches)


def _announce_into_the_event_log(state_dir: Path) -> None:
    handler = queued_event_log(event_log_path(state_dir),
                               after_an_announcement=partial(wake_the_dashboard, state_dir))
    handler.setLevel(MESSAGE)
    logging.getLogger("fun_time_vr").addHandler(handler)


def _run(manifest: LaunchManifest, vr: VrSettings, reaches: WhatTheHeadsetReaches) -> int:
    opened = reaches.open_session()
    if opened.session is None:
        reaches.say_why(opened.message)
        return 1
    session = opened.session
    headset = Headset(session, reaches.renderer(), FramePerf(logger=logger))
    state_dir = Path(manifest.commands.dashboard_cmd_file).parent
    loading_screen = reaches.loading_screen(state_dir)
    raise_the_loading_screen(headset, loading_screen)
    the_room = reaches.hang_the_room(
        RoomLaunch(manifest, vr, session.shared_contexts(), headset.perf),
        loading_screen=loading_screen,
        present_the_loading_screen=lambda: present_the_loading_screen(headset, loading_screen))
    the_room.start_pumping(headset.perf)
    logger.info(
        "Entering the VR loop (three Funestras up, compositor layers %s)",
        "on" if vr.compositor_layers else "off",
    )
    try:
        FrameLoop(headset, the_room, use_layers=vr.compositor_layers).show_the_room(
            reaches.window_events())
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        tear_down(headset, the_room, state_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
