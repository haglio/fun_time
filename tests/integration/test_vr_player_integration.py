"""Integration: the VR player's whole pipeline minus the headset.

Builds the real units (`_PrimaryUnit`, two `_SatelliteUnit`s) from a manifest
produced by the production `build_vr_manifest`, decodes real library media
through real mpv render contexts into GL textures on a hidden GLFW context,
runs the production file-channel worker beside the frame loop, and paces the
loop at the headset's 90Hz.  Everything FunTimeVR does except OpenXR itself.

The frame-budget assertion is the regression guard this suite exists for:
libmpv's render call blocks until the frame's own display time unless told
not to, which paced the whole session at the videos' 30fps — the exact
"runs but not smooth" the headset showed.  A reintroduced blocker pins the
loop's median at a video frame period and fails here loudly.
"""
from __future__ import annotations

import glob
import os
import random
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from player_core.file_channel import append_command

from fun_time.config import load_config
from fun_time.dashboard_runtime import read_nau_status
from fun_time.manifest import write_manifest_data
from fun_time.satellite_control import read_satellite_status
from fun_time_vr.orchestrator import build_vr_manifest

from .integration_support import build_integration_config, build_integration_temp_root

# One headset refresh period at the Crystal Super's 90Hz.
FRAME_BUDGET_MS = 1000.0 / 90.0


def _wait(predicate, *, timeout, desc):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.2)
    pytest.fail(f"timed out waiting for {desc} (last={last!r})")


def _sample_library_videos(dirs, count: int) -> list[str]:
    candidates: list[str] = []
    for root in dirs:
        candidates.extend(
            glob.glob(os.path.join(str(root), "**", "*.mp4"), recursive=True)
        )
    assert len(candidates) >= count, f"need {count} sample videos under {dirs}"
    return random.sample(candidates, count)


def test_vr_pipeline_holds_frame_budget_and_obeys_the_channels():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    config = load_config(config_path)

    manifest_data = build_vr_manifest(config)
    manifest_path = write_manifest_data(
        manifest_data, config.paths.state_dir / "windows_bridge_launch.ini"
    )

    import glfw  # noqa: PLC0415 — the GL stack loads only inside the test

    import fun_time_vr.player as vrp  # noqa: PLC0415

    manifest = vrp._read_manifest(manifest_path)
    commands = manifest["commands"]

    # The primary rotates real VR-library masters when the machine has them
    # (the realistic heavy-decode load), and falls back to the desktop primary
    # rotation's own files — the same merged-sources order production uses.
    primary_videos = _sample_library_videos(
        [*config.vr.library_dirs, *config.paths.nau_library_dirs], 2
    )
    Path(commands["nau_playlist_file"]).write_text(
        "".join(f"{video}\n" for video in primary_videos), encoding="utf-8"
    )
    Path(commands["portrait_playlist_file"]).write_text(
        "".join(f"{video}\n" for video in _sample_library_videos(config.paths.portrait_dirs, 2)),
        encoding="utf-8",
    )
    Path(commands["landscape_playlist_file"]).write_text(
        "".join(f"{video}\n" for video in _sample_library_videos(config.paths.landscape_dirs, 2)),
        encoding="utf-8",
    )
    for side in ("nau", "portrait", "landscape"):
        Path(commands[f"{side}_paused_file"]).write_text("0", encoding="utf-8")

    assert glfw.init(), "glfw failed to initialize"
    glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 4)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 5)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    window = glfw.create_window(320, 200, "vr-pipeline-test", None, None)
    assert window, "hidden GL window could not be created"
    glfw.make_context_current(window)

    from OpenGL import GL  # noqa: PLC0415

    from fun_time_vr.render import SceneRenderer, immersive_mode  # noqa: PLC0415

    renderer = SceneRenderer()
    primary = vrp._PrimaryUnit(manifest, glfw.get_proc_address)
    satellites = [
        vrp._SatelliteUnit("portrait", manifest, glfw.get_proc_address),
        vrp._SatelliteUnit("landscape", manifest, glfw.get_proc_address),
    ]
    units = [primary, *satellites]
    stop = threading.Event()
    perf = vrp.FramePerf(logger=vrp.logger)
    pump_thread = threading.Thread(
        target=vrp._pump_channels, args=(units, stop, perf), daemon=True,
        name="file-channels",
    )

    def has_picture(unit) -> bool:
        """Whether a horizontal strip through the target's middle holds any
        non-black pixel — resilient to a clip that opens on a dark scene."""
        strip = np.zeros((1, 64, 4), dtype=np.uint8)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, unit.target.fbo)
        GL.glReadPixels(
            max(0, unit.target.width // 2 - 32), unit.target.height // 2, 64, 1,
            GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, strip,
        )
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        return bool(strip[:, :, :3].any())

    frame_ms: list[float] = []
    period = FRAME_BUDGET_MS / 1e3

    def run_frames(count: int, *, measure: bool) -> None:
        for _ in range(count):
            started = time.perf_counter()
            for unit in units:
                unit.render_latest_frame()
            glfw.poll_events()
            elapsed = time.perf_counter() - started
            if measure:
                frame_ms.append(elapsed * 1e3)
            time.sleep(max(0.0, period - elapsed))

    try:
        pump_thread.start()

        # Status flows from the worker before any frame renders — the
        # orchestrator's startup gate reads this exact file.
        _wait(
            lambda: read_nau_status(Path(commands["nau_status_file"])).video,
            timeout=30, desc="the primary's first status write",
        )

        # All three players decode into their textures.
        run_frames(240, measure=False)  # warm-up: files open, targets allocate
        for unit, name in ((primary, "primary"), (satellites[0], "portrait"), (satellites[1], "landscape")):
            _wait(
                lambda u=unit: (run_frames(9, measure=False) or u.target.ready),
                timeout=30, desc=f"{name} target allocation",
            )
        _wait(
            lambda: (run_frames(9, measure=False) or any(has_picture(unit) for unit in units)),
            timeout=20, desc="a unit to render a non-black frame",
        )

        # The regression guard: three live decoders must not pace the loop.
        run_frames(540, measure=True)
        frame_ms.sort()
        median = frame_ms[len(frame_ms) // 2]
        assert median < FRAME_BUDGET_MS, (
            f"frame loop median {median:.1f}ms blows the {FRAME_BUDGET_MS:.1f}ms budget — "
            "an mpv render is pacing the loop again"
        )

        # Commands travel the file channel through the worker thread.
        first_video = read_nau_status(Path(commands["nau_status_file"])).video
        append_command(Path(commands["nau_cmd_file"]), "NEXT")
        _wait(
            lambda: read_nau_status(Path(commands["nau_status_file"])).video
            not in ("", first_video),
            timeout=20, desc="NEXT to advance the primary",
        )

        # The paused flag freezes a satellite where it stands.
        Path(commands["portrait_paused_file"]).write_text("1", encoding="utf-8")
        _wait(
            lambda: read_satellite_status(Path(commands["portrait_status_file"])).paused,
            timeout=10, desc="the portrait satellite to report paused",
        )
        position_before = read_satellite_status(Path(commands["portrait_status_file"])).position_ms
        run_frames(90, measure=False)
        time.sleep(0.3)  # one worker tick past the last status write
        position_after = read_satellite_status(Path(commands["portrait_status_file"])).position_ms
        assert position_after == position_before, (
            f"paused satellite kept playing ({position_before} -> {position_after})"
        )

        # The projection resolved for the playing video is a renderable mode:
        # either an immersive wrap this renderer has a shader for, or flat.
        from fun_time_vr.projection import PROJECTIONS  # noqa: PLC0415

        assert primary.role.projection in PROJECTIONS
        assert primary.role.projection == "flat" or (
            immersive_mode(primary.role.projection) is not None
        )
    finally:
        stop.set()
        pump_thread.join(timeout=5.0)
        for unit in units:
            unit.close()
        renderer.close()
        glfw.terminate()
