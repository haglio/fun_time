"""The headset process: the session's three Funestras composited into one
OpenXR scene.

The desktop session runs the Main Funestra and the two satellites as processes
owning windows; an OpenXR runtime gives the headset to one rendering process, so
every Funestra here is a surface of this one.  What hangs in the room and how the
controllers move it is :mod:`fun_time_vr.funestras`, and what each frame shows
is :mod:`fun_time_vr.frame_loop`, both tested off the headset; this is the shell
around them, which needs one: the OpenXR session and the GL it draws with.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from app_support.logging_utils import install_exception_logging
from app_support.win32 import set_app_user_model_id

from fun_time import preview_marker
from fun_time.event_log import MESSAGE, EventLogHandler, event_log_path
from fun_time.manifest import LaunchManifest
from fun_time.project_paths import PROJECT_VR_ICON

from . import vr_runtime
from .bringup import open_vr_session
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
    # The strip that shows this process's messages tails the session's event log,
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
    handler.setLevel(MESSAGE)  # announcements only; the log file carries the rest
    logging.getLogger("fun_time_vr").addHandler(handler)


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
    headset = Headset(session, SceneRenderer(), FramePerf(logger=logger))
    state_dir = Path(manifest.commands.dashboard_cmd_file).parent
    # Before the Funestras and refreshed between them: each opens media.
    loading_screen = LoadingScreenUnit(state_dir)
    raise_the_loading_screen(headset, loading_screen)
    the_room = hang_the_room(
        manifest, vr, session.shared_contexts(), manifest_path=manifest_path, loading_screen=loading_screen,
        perf=headset.perf, present_the_loading_screen=lambda: present_the_loading_screen(headset, loading_screen))
    the_room.start_pumping(headset.perf)
    logger.info(
        "Entering the VR loop (three Funestras up, compositor layers %s)",
        "on" if vr.compositor_layers else "off",
    )
    try:
        FrameLoop(headset, the_room, use_layers=vr.compositor_layers).show_the_room(glfw)
    except KeyboardInterrupt:
        logger.info("Interrupted")
    finally:
        tear_down(headset, the_room, state_dir)
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
