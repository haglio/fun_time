"""Integration: the VR player's whole pipeline minus the headset.

Builds the real units (`_MainUnit`, two `_SatelliteUnit`s) from a manifest
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

import os
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from player_core.file_channel import append_command

from fun_time.config import load_config
from fun_time.manifest import LaunchManifest, write_manifest_data
from fun_time.player_status import read_nau_status
from fun_time.runtime_flow import apply_mode_switch
from fun_time.satellite_control import read_satellite_status
from fun_time_vr.layout import (
    DEFAULT_LAYOUT,
    LANDSCAPE,
    LAYOUT_FILENAME,
    PORTRAIT,
    PRIMARY,
    read_layout,
)
from fun_time_vr.orchestrator import build_vr_manifest

from .integration_support import (
    build_integration_config,
    build_integration_temp_root,
    library_clips,
    readable_at_speed,
    sample_library_clips,
    stall_per_transition,
    window_is_quiet,
)

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]

# One headset refresh period at the Crystal Super's 90Hz.
FRAME_BUDGET_MS = 1000.0 / 90.0
# What the median gates compare against.  The regression they guard — an mpv
# render PACING the loop (video_dims querying a locked core) — showed as
# hundreds of milliseconds per frame; a healthy loop measures 1-2ms mid-suite,
# so half a period of headroom is margin the machine can spend, not the
# pipeline.
MEDIAN_BUDGET_MS = FRAME_BUDGET_MS * 1.5
# How many 120-frame windows the settle probe below will spend waiting for a
# quiet one.  Generous: what it is waiting out is a whole session's teardown,
# seconds of it, and every window it spends is one the sample does not have to
# distrust.
SETTLE_WINDOWS = 12


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
    return [str(clip) for clip in sample_library_clips(
        library_clips(dirs), count,
        desc=f"sample videos under {dirs}", readable=readable_at_speed,
    )]


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

    manifest = LaunchManifest.read(manifest_path)
    vr = vrp.VrSettings.read(manifest_path)
    commands = manifest.commands

    # The desktop library, and never the VR masters: those sit on the cloud drive,
    # where opening a cold file blocks inside the drive's own driver.  No timeout
    # can end a thread stuck there, and Windows cannot finish closing a process
    # that has one, so every run that read them left an unkillable python process.
    main_videos = _sample_library_videos(config.paths.nau_library_dirs, 2)
    Path(commands.nau_playlist_file).write_text(
        "".join(f"{video}\n" for video in main_videos), encoding="utf-8"
    )
    Path(commands.portrait_playlist_file).write_text(
        "".join(f"{video}\n" for video in _sample_library_videos(config.paths.portrait_dirs, 2)),
        encoding="utf-8",
    )
    Path(commands.landscape_playlist_file).write_text(
        "".join(f"{video}\n" for video in _sample_library_videos(config.paths.landscape_dirs, 2)),
        encoding="utf-8",
    )
    for side in ("nau", "portrait", "landscape"):
        Path(commands.side_file(side, "paused")).write_text("0", encoding="utf-8")

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
    layout = read_layout(config.paths.state_dir / LAYOUT_FILENAME)
    main = vrp._MainUnit(manifest, vr, glfw.get_proc_address, placement=layout[PRIMARY])
    satellites = [
        vrp._SatelliteUnit(
            side, manifest, glfw.get_proc_address, vr=vr, placement=layout[side])
        for side in (PORTRAIT, LANDSCAPE)
    ]
    units = [main, *satellites]
    stop = threading.Event()
    perf = vrp.FramePerf(logger=vrp.logger)
    pump_failure: list[BaseException] = []

    def pump_channels() -> None:
        """The production worker, with whatever it raises kept for the test.

        What it does *after* the players close is the point of the teardown
        check at the end, and a worker that died quietly in that window would
        otherwise look exactly like one that had nothing left to do.
        """
        try:
            vrp._pump_channels(units, stop, perf)
        except BaseException as exc:  # noqa: BLE001 — re-raised, and reported
            pump_failure.append(exc)
            raise

    pump_thread = threading.Thread(
        target=pump_channels, daemon=True, name="file-channels",
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

    def run_frames(count: int, *, measure: bool, sink: list[float] | None = None) -> None:
        if measure and sink is None:
            sink = frame_ms
        for _ in range(count):
            started = time.perf_counter()
            for unit in units:
                unit.render_latest_frame()
            glfw.poll_events()
            elapsed = time.perf_counter() - started
            if measure:
                sink.append(elapsed * 1e3)
            time.sleep(max(0.0, period - elapsed))

    closed = False
    try:
        pump_thread.start()

        # Status flows from the worker before any frame renders — the
        # orchestrator's startup gate reads this exact file.
        _wait(
            lambda: read_nau_status(Path(commands.nau_status_file)).video,
            timeout=30, desc="the main player's first status write",
        )

        # All three players decode into their textures.
        run_frames(240, measure=False)  # warm-up: files open, targets allocate
        for unit, name in ((main, "main"), (satellites[0], "portrait"), (satellites[1], "landscape")):
            _wait(
                lambda u=unit: (run_frames(9, measure=False) or u.target.ready),
                timeout=30, desc=f"{name} target allocation",
            )
        _wait(
            lambda: (run_frames(9, measure=False) or any(has_picture(unit) for unit in units)),
            timeout=20, desc="a unit to render a non-black frame",
        )

        # Hold the measurement until the machine has settled: in a full suite
        # run this test starts moments after whole sessions were torn down,
        # and their kill sweeps and mpv teardown bleed into the first seconds
        # here — a blown median that indicts the neighbors, not the pipeline.
        # Probe in short windows and start the real sample only once one comes
        # in quiet; bounded, and a machine that never gets there says so in
        # those words rather than leaving the sample to blame the pipeline.
        for _ in range(SETTLE_WINDOWS):
            probe: list[float] = []
            run_frames(120, measure=True, sink=probe)
            if window_is_quiet(probe, budget_ms=FRAME_BUDGET_MS):
                break
        else:
            probe.sort()
            pytest.fail(
                f"the machine never rendered {SETTLE_WINDOWS} quiet windows into one: "
                f"the last was {probe[len(probe) // 2]:.1f}ms median, "
                f"{probe[len(probe) * 9 // 10]:.1f}ms p90, {probe[-1]:.1f}ms worst "
                f"against a {FRAME_BUDGET_MS:.1f}ms frame — nothing was measured"
            )

        # The regression guard: three live decoders must not pace the loop.
        run_frames(540, measure=True)
        frame_ms.sort()
        median = frame_ms[len(frame_ms) // 2]
        assert median < MEDIAN_BUDGET_MS, (
            f"frame loop median {median:.1f}ms blows the {MEDIAN_BUDGET_MS:.1f}ms budget — "
            "an mpv render is pacing the loop again"
        )

        # Commands travel the file channel through the worker thread.
        first_video = read_nau_status(Path(commands.nau_status_file)).video
        append_command(Path(commands.nau_cmd_file), "NEXT")
        _wait(
            lambda: read_nau_status(Path(commands.nau_status_file)).video
            not in ("", first_video),
            timeout=20, desc="NEXT to advance the main player",
        )

        # Clip transitions must not stall the frame loop.  Cold-load the
        # landscape satellite repeatedly — explicit navigation, the harsher
        # path than prefetched rollover — while frames keep pace.  Before
        # video_dims stopped querying mpv's core (which a file being opened
        # holds locked), each transition blocked the render thread for
        # hundreds of milliseconds and every screen in the scene hitched.
        transitions: list[list[float]] = []
        for _ in range(4):
            append_command(Path(commands.landscape_cmd_file), "NEXT")
            transitions.append([])
            for _ in range(60):
                started = time.perf_counter()
                for unit in units:
                    unit.render_latest_frame()
                glfw.poll_events()
                elapsed = time.perf_counter() - started
                transitions[-1].append(elapsed * 1e3)
                time.sleep(max(0.0, period - elapsed))
        transition_ms = sorted(ms for one in transitions for ms in one)
        transition_median = transition_ms[len(transition_ms) // 2]
        assert transition_median < MEDIAN_BUDGET_MS, (
            f"frame loop median {transition_median:.1f}ms during clip transitions "
            f"blows the {MEDIAN_BUDGET_MS:.1f}ms budget"
        )
        # The regression this guards stalled EVERY transition for hundreds of
        # milliseconds (an mpv core query on the render thread), so what a
        # transition TYPICALLY costs is what says whether it is back.  Judged
        # on the worst frame instead, this rode the one statistic a machine's
        # own hiccup owns: a run whose four transitions cost 79, 91, 92 and
        # 628ms is a hiccup on one pass, and it failed here as a pipeline
        # that stalls on every clip change.
        stall = stall_per_transition(transitions)
        assert stall < 150.0, (
            f"clip transitions stalled the frame loop {stall:.0f}ms apiece "
            f"(worst frames {[round(max(one)) for one in transitions]}) — "
            "an mpv core query is back on the render thread"
        )

        # The paused flag freezes a satellite where it stands.
        Path(commands.portrait_paused_file).write_text("1", encoding="utf-8")
        _wait(
            lambda: read_satellite_status(Path(commands.portrait_status_file)).paused,
            timeout=10, desc="the portrait satellite to report paused",
        )
        position_before = read_satellite_status(Path(commands.portrait_status_file)).position_ms
        run_frames(90, measure=False)
        time.sleep(0.3)  # one worker tick past the last status write
        position_after = read_satellite_status(Path(commands.portrait_status_file)).position_ms
        assert position_after == position_before, (
            f"paused satellite kept playing ({position_before} -> {position_after})"
        )

        # The projection resolved for the playing video is a renderable mode:
        # either an immersive wrap this renderer has a shader for, or flat.
        from fun_time_vr.projection import PROJECTIONS  # noqa: PLC0415

        assert main.role.projection in PROJECTIONS
        assert main.role.projection == "flat" or (
            immersive_mode(main.role.projection) is not None
        )

        # Teardown, deliberately in the hostile order: the players close while
        # the worker is still pumping them.  Production reaches this state
        # whenever its `join(timeout=...)` returns with the pump still inside
        # libmpv — and freeing a render context and terminating a core under a
        # live `mpv_get_property` faulted the reader every single time it was
        # staged.  Each player now bars its own gate, waits out the call in
        # flight and no-ops the rest (`player_core.mpv_gate`), so this order is
        # safe; staging it here is what makes a regression fail deterministically
        # rather than once in nine full-suite runs.  It fails LOUDLY — a
        # reintroduced use-after-free takes the whole pytest process down here.
        for unit in units:
            unit.close()
        closed = True
        time.sleep(0.5)  # more worker turns against players that are already gone
        stop.set()
        pump_thread.join(timeout=30.0)
        assert not pump_thread.is_alive(), (
            "the file-channel worker never came back after the players closed"
        )
        assert not pump_failure, (
            f"the worker raised once the players were closed: {pump_failure[0]!r}"
        )
    finally:
        stop.set()
        if not closed:
            for unit in units:
                unit.close()
        renderer.close()
        glfw.terminate()


def test_the_main_player_plays_once_video_mode_unpauses_it():
    """He put the headset on in genau mode, said "video mode", and the main
    player sat on one frame for the rest of the session -- unpaused, its
    duration known, its position stuck at zero.  This walks that exact path: the
    main player comes up paused the way a genau-mode session leaves it, and the
    PRODUCTION mode switch is what unpauses it."""
    temp_root = build_integration_temp_root()
    config = load_config(build_integration_config(temp_root))
    manifest_path = write_manifest_data(
        build_vr_manifest(config), config.paths.state_dir / "windows_bridge_launch.ini"
    )

    import glfw  # noqa: PLC0415 — the GL stack loads only inside the test

    import fun_time_vr.player as vrp  # noqa: PLC0415

    manifest = LaunchManifest.read(manifest_path)
    vr = vrp.VrSettings.read(manifest_path)
    commands = manifest.commands
    Path(commands.nau_playlist_file).write_text(
        "".join(f"{video}\n" for video in _sample_library_videos(
            config.paths.nau_library_dirs, 2)),
        encoding="utf-8",
    )
    # Genau mode is where the main player waits paused, and the flag survives a
    # session end -- so this is the state a headset session opens in.
    Path(commands.nau_paused_file).write_text("1", encoding="utf-8")

    assert glfw.init(), "glfw failed to initialize"
    glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 4)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 5)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    window = glfw.create_window(320, 200, "vr-play-test", None, None)
    assert window, "hidden GL window could not be created"
    glfw.make_context_current(window)

    main = vrp._MainUnit(manifest, vr, glfw.get_proc_address,
                         placement=DEFAULT_LAYOUT[PRIMARY])
    stop = threading.Event()
    pump = threading.Thread(
        target=vrp._pump_channels, args=([main], stop, vrp.FramePerf(logger=vrp.logger)),
        daemon=True, name="file-channels",
    )

    def run_frames(count: int) -> None:
        for _ in range(count):
            main.render_latest_frame()
            glfw.poll_events()
            time.sleep(FRAME_BUDGET_MS / 1e3)

    try:
        pump.start()
        _wait(lambda: read_nau_status(Path(commands.nau_status_file)).duration_ms,
              timeout=30, desc="the main player to open its video")
        run_frames(120)
        assert read_nau_status(Path(commands.nau_status_file)).paused, (
            "the main player should still be holding where genau mode left it"
        )

        apply_mode_switch(
            current_mode="genau", target_mode="video", omni_paused=False,
            genau_cmd_file=commands.genau_cmd_file,
            nau_paused_file=commands.nau_paused_file,
            nau_cmd_file=commands.nau_cmd_file,
        )

        position = _wait(
            lambda: (run_frames(9) or read_nau_status(Path(commands.nau_status_file)).position_ms),
            timeout=30,
            desc="the main player's position to advance once video mode unpaused it",
        )
        assert position > 0
        assert main.role.displayed, "DISPLAY_ON rides the switch into video mode"
    finally:
        stop.set()
        pump.join(timeout=5.0)
        main.close()
        glfw.terminate()
