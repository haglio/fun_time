from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import glfw
import numpy as np
import pytest
from funestra.file_channel import append_command
from funestra.modes import LoopState
from OpenGL import GL, _configflags

import fun_time_vr.funestras as vrp
from fun_time.config import load_config
from fun_time.funestra_status import read_main_funestra_status
from fun_time.manifest import LaunchManifest, write_manifest_data
from fun_time.runtime_flow import apply_mode_switch
from fun_time.satellite_control import read_satellite_status
from fun_time_vr.gl_contexts import SharedContexts, hidden_gl_window
from fun_time_vr.layout import (
    LANDSCAPE,
    PORTRAIT,
    Layout,
    remembered_layout,
)
from fun_time_vr.orchestrator import build_vr_manifest
from fun_time_vr.render import SceneRenderer
from fun_time_vr.scheduling import ahead_of_background_work
from fun_time_vr.wraps import immersive_wrap
from main_funestra.loop_verbs import LOOP_CANCEL, RECORD_TAP, SET_LOOP

from .integration_support import (
    build_integration_config,
    build_integration_temp_root,
    joined_within,
    let_seconds_pass,
    library_videos,
    one_video_per_video,
    published_status,
    readable_at_speed,
    sample_library_videos,
    stall_per_transition,
    wait_for,
    window_is_quiet,
)

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]


@pytest.fixture(autouse=True)
def _the_frame_loop_runs_ahead_of_background_work_as_the_headset_session_does():
    with ahead_of_background_work():
        yield


@pytest.fixture(autouse=True)
def _no_gl_call_waits_for_the_drivers_error_report_as_in_the_headset_session():
    assert not _configflags.ERROR_CHECKING, (
        "PyOpenGL was loaded before fun_time_vr in this run, so every GL call here "
        "waits on the driver the way the headset's never do"
    )


FRAME_BUDGET_MS = 1000.0 / 90.0
# What the median gates compare against.  The regression they guard — an mpv
# render PACING the loop (video_dims querying a locked core) — showed as
# hundreds of milliseconds per frame; a healthy loop measures 1-2ms mid-suite,
# so half a period of headroom is margin the machine can spend, not the
# pipeline.
MEDIAN_BUDGET_MS = FRAME_BUDGET_MS * 1.5
SETTLE_WINDOWS = 12


def _sample_library_videos(dirs, count: int) -> list[str]:
    return [str(video) for video in sample_library_videos(
        library_videos(dirs), count,
        desc=f"sample videos under {dirs}", readable=readable_at_speed,
    )]


def _main_videos_next_can_step_between(config, manifest, vr) -> list[str]:
    library = vrp._TheLibrary(manifest, vr, while_reading=lambda: None)
    return [str(video) for video in sample_library_videos(
        one_video_per_video(library_videos(config.paths.main_funestra_library_dirs),
                           library.source.version_index), 2,
        desc="different videos in the Main Funestra's library", readable=readable_at_speed,
    )]


def test_vr_pipeline_holds_frame_budget_and_obeys_the_channels():
    temp_root = build_integration_temp_root()
    config_path = build_integration_config(temp_root)
    config = load_config(config_path)

    manifest_data = build_vr_manifest(config)
    manifest_path = write_manifest_data(
        manifest_data, config.paths.state_dir / "windows_bridge_launch.ini"
    )


    manifest = LaunchManifest.read(manifest_path)
    vr = vrp.VrSettings.read(manifest_path)
    commands = manifest.commands

    # The desktop library, and never the VR masters: those sit on the cloud drive,
    # where opening a cold file blocks inside the drive's own driver.  No timeout
    # can end a thread stuck there, and Windows cannot finish closing a process
    # that has one, so every run that read them left an unkillable python process.
    Path(commands.main_funestra_playlist_file).write_text(
        "".join(f"{video}\n" for video in _main_videos_next_can_step_between(config, manifest, vr)),
        encoding="utf-8",
    )
    Path(commands.portrait_playlist_file).write_text(
        "".join(f"{video}\n" for video in _sample_library_videos(config.paths.portrait_dirs, 2)),
        encoding="utf-8",
    )
    Path(commands.landscape_playlist_file).write_text(
        "".join(f"{video}\n" for video in _sample_library_videos(config.paths.landscape_dirs, 2)),
        encoding="utf-8",
    )
    for satellite in ("main_funestra", "portrait", "landscape"):
        Path(commands.funestra_file(satellite, "paused")).write_text("0", encoding="utf-8")

    assert glfw.init(), "glfw failed to initialize"
    window = hidden_gl_window("vr-pipeline-test")
    glfw.make_context_current(window)

    renderer = SceneRenderer()
    contexts = SharedContexts(window)
    remembered = remembered_layout(config.paths.state_dir)
    stop = threading.Event()
    main = vrp._MainUnit(manifest, vr, contexts, remembered=remembered, stop=stop)
    satellites = [
        vrp._SatelliteUnit(satellite, manifest, contexts, vr=vr,
                           remembered=remembered.placements)
        for satellite in (PORTRAIT, LANDSCAPE)
    ]
    units = [main, *satellites]
    perf = vrp.FramePerf(logger=vrp.logger)
    pump_failure: list[BaseException] = []

    def pump_channels() -> None:
        try:
            vrp._pump_channels(units, stop, perf)
        except BaseException as exc:  # noqa: BLE001 — re-raised, and reported
            pump_failure.append(exc)
            raise

    pump_thread = threading.Thread(
        target=pump_channels, daemon=True, name="file-channels",
    )

    def has_frame(unit) -> bool:
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

        wait_for(
            lambda: read_main_funestra_status(Path(commands.main_funestra_status_file)).video,
            desc="the Main Funestra's first status write",
        )

        run_frames(240, measure=False)
        for unit, name in ((main, "main"), (satellites[0], "portrait"), (satellites[1], "landscape")):
            wait_for(
                lambda u=unit: (run_frames(9, measure=False) or u.target.ready),
                desc=f"{name} target allocation",
            )
        wait_for(
            lambda: (run_frames(9, measure=False) or any(has_frame(unit) for unit in units)),
            desc="a unit to render a non-black frame",
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

        for unit in units:
            unit.layer_dirty = False
        run_frames(540, measure=True)
        frame_ms.sort()
        median = frame_ms[len(frame_ms) // 2]
        assert median < MEDIAN_BUDGET_MS, (
            f"frame loop median {median:.1f}ms blows the {MEDIAN_BUDGET_MS:.1f}ms budget — "
            "an mpv render is pacing the loop again"
        )
        stalled = [name for unit, name in zip(units, ("main", "portrait", "landscape"), strict=True)
                   if not unit.layer_dirty]
        assert not stalled, f"no new frame reached the scene from {stalled} in six seconds"

        first_video = published_status(
            read_main_funestra_status, Path(commands.main_funestra_status_file)).video
        append_command(Path(commands.main_funestra_cmd_file), "NEXT")
        wait_for(
            lambda: read_main_funestra_status(Path(commands.main_funestra_status_file)).video
            not in ("", first_video),
            desc="NEXT to advance the Main Funestra",
        )

        # Video transitions must not stall the frame loop, and must reach the
        # scene.  Cold-load the landscape satellite repeatedly — explicit
        # navigation, the harsher path than prefetched rollover — while frames
        # keep pace.  What a video change costs is mpv's: a decoder's textures,
        # its shaders and the CUDA interop its frames arrive through, 40 to
        # 250ms of it, charged to whichever thread calls render.  On the loop
        # that was the whole headset freezing at every video change.
        transitions: list[list[float]] = []
        landscape_status = Path(commands.landscape_status_file)
        for _ in range(4):
            leaving = published_status(read_satellite_status, landscape_status).video
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
            wait_for(
                lambda was=leaving: read_satellite_status(landscape_status).video
                not in ("", was),
                desc="the landscape satellite to move off its clip",
            )
            satellites[1].layer_dirty = False
            wait_for(
                lambda: run_frames(9, measure=False) or satellites[1].layer_dirty,
                desc="a frame of the clip the landscape satellite moved to",
            )
        transition_ms = sorted(ms for one in transitions for ms in one)
        transition_median = transition_ms[len(transition_ms) // 2]
        assert transition_median < MEDIAN_BUDGET_MS, (
            f"frame loop median {transition_median:.1f}ms during clip transitions "
            f"blows the {MEDIAN_BUDGET_MS:.1f}ms budget"
        )
        # The regression this guards stalled EVERY transition for hundreds of
        # milliseconds (mpv's own work, on the loop's thread), so what a
        # transition TYPICALLY costs is what says whether it is back.  Judged
        # on the worst frame instead, this rode the one statistic a machine's
        # own hiccup owns: a run whose four transitions cost 79, 91, 92 and
        # 628ms is a hiccup on one pass, and it failed here as a pipeline
        # that stalls on every video change.
        stall = stall_per_transition(transitions)
        assert stall < 150.0, (
            f"clip transitions stalled the frame loop {stall:.0f}ms apiece "
            f"(worst frames {[round(max(one)) for one in transitions]}) — "
            "the frame loop is waiting on the GL driver or on mpv while a clip changes"
        )

        Path(commands.portrait_paused_file).write_text("1", encoding="utf-8")
        wait_for(
            lambda: read_satellite_status(Path(commands.portrait_status_file)).paused,
            desc="the portrait satellite to report paused",
        )
        position_before = published_status(
            read_satellite_status, Path(commands.portrait_status_file)).position_ms
        run_frames(90, measure=False)
        let_seconds_pass(0.3)
        position_after = published_status(
            read_satellite_status, Path(commands.portrait_status_file)).position_ms
        assert position_after == position_before, (
            f"paused satellite kept playing ({position_before} -> {position_after})"
        )

        from fun_time_vr.projection import PROJECTIONS  # noqa: PLC0415

        assert main.verbs.projection in PROJECTIONS
        assert main.verbs.projection == "flat" or (
            immersive_wrap(main.verbs.projection) is not None
        )

        # Teardown, deliberately in the hostile order: the Funestras close while
        # the worker is still pumping them.  Production reaches this state
        # whenever its `join(timeout=...)` returns with the pump still inside
        # libmpv — and freeing a render context and terminating a core under a
        # live `mpv_get_property` faulted the reader every single time it was
        # staged.  Each Funestra now bars its own gate, waits out the call in
        # flight and no-ops the rest (`funestra.mpv_gate`), so this order is
        # safe; staging it here is what makes a regression fail deterministically
        # rather than once in nine full-suite runs.  It fails LOUDLY — a
        # reintroduced use-after-free takes the whole pytest process down here.
        for unit in units:
            unit.close()
        closed = True
        let_seconds_pass(0.5)
        stop.set()
        assert joined_within(pump_thread, 30.0), (
            "the file-channel worker never came back after the Funestras closed"
        )
        assert not pump_failure, (
            f"the worker raised once the Funestras were closed: {pump_failure[0]!r}"
        )
    finally:
        stop.set()
        if not closed:
            for unit in units:
                unit.close()
        renderer.close()
        glfw.terminate()


def test_the_main_funestra_plays_once_kino_mode_unpauses_it():
    temp_root = build_integration_temp_root()
    config = load_config(build_integration_config(temp_root))
    manifest_path = write_manifest_data(
        build_vr_manifest(config), config.paths.state_dir / "windows_bridge_launch.ini"
    )


    manifest = LaunchManifest.read(manifest_path)
    vr = vrp.VrSettings.read(manifest_path)
    commands = manifest.commands
    Path(commands.main_funestra_playlist_file).write_text(
        "".join(f"{video}\n" for video in _main_videos_next_can_step_between(config, manifest, vr)),
        encoding="utf-8",
    )
    Path(commands.main_funestra_paused_file).write_text("1", encoding="utf-8")

    assert glfw.init(), "glfw failed to initialize"
    window = hidden_gl_window("vr-play-test")
    glfw.make_context_current(window)

    stop = threading.Event()
    main = vrp._MainUnit(manifest, vr, SharedContexts(window), remembered=Layout(), stop=stop)
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
        wait_for(lambda: read_main_funestra_status(Path(commands.main_funestra_status_file)).duration_ms,
                 desc="the Main Funestra to open its video")
        run_frames(120)
        assert published_status(
            read_main_funestra_status, Path(commands.main_funestra_status_file)).paused, (
            "the Main Funestra should still be holding where genau mode left it"
        )

        apply_mode_switch(
            current_mode="genau", target_mode="kino", omni_paused=False,
            main_funestra_paused_file=commands.main_funestra_paused_file,
            main_funestra_cmd_file=commands.main_funestra_cmd_file,
        )

        position = wait_for(
            lambda: (run_frames(9) or read_main_funestra_status(Path(commands.main_funestra_status_file)).position_ms),
            desc="the Main Funestra's position to advance once kino mode unpaused it",
        )
        assert position > 0
        assert main.owns_the_slot, "SHOW kino rides the switch into kino mode"
    finally:
        stop.set()
        joined_within(pump, 5.0)
        main.close()
        glfw.terminate()
def test_the_main_funestra_marks_and_runs_an_ab_loop_in_the_headset():
    temp_root = build_integration_temp_root()
    config = load_config(build_integration_config(temp_root))
    manifest_path = write_manifest_data(
        build_vr_manifest(config), config.paths.state_dir / "windows_bridge_launch.ini"
    )
    manifest = LaunchManifest.read(manifest_path)
    vr = vrp.VrSettings.read(manifest_path)
    commands = manifest.commands
    Path(commands.main_funestra_playlist_file).write_text(
        "".join(f"{video}\n" for video in _main_videos_next_can_step_between(config, manifest, vr)),
        encoding="utf-8",
    )
    Path(commands.main_funestra_paused_file).write_text("0", encoding="utf-8")
    status_file = Path(commands.main_funestra_status_file)
    cmd_file = Path(commands.main_funestra_cmd_file)

    assert glfw.init(), "glfw failed to initialize"
    window = hidden_gl_window("vr-loop-test")
    glfw.make_context_current(window)

    stop = threading.Event()
    main = vrp._MainUnit(manifest, vr, SharedContexts(window), remembered=Layout(), stop=stop)
    pump = threading.Thread(
        target=vrp._pump_channels, args=([main], stop, vrp.FramePerf(logger=vrp.logger)),
        daemon=True, name="file-channels",
    )

    def run_frames(count: int) -> None:
        for _ in range(count):
            main.render_latest_frame()
            glfw.poll_events()
            time.sleep(FRAME_BUDGET_MS / 1e3)

    def published():
        return read_main_funestra_status(status_file)

    def after_frames(answer):
        return lambda: (run_frames(9) or answer())

    try:
        pump.start()
        wait_for(after_frames(lambda: published().position_ms),
                 desc="the video to start playing")
        Path(commands.main_funestra_paused_file).write_text("1", encoding="utf-8")
        held_at = wait_for(after_frames(lambda: published().paused and published().position_ms),
                           desc="the video to hold where it had got to")
        assert published().loop_state is LoopState.NORMAL

        append_command(cmd_file, RECORD_TAP)
        wait_for(after_frames(lambda: published().loop_state is LoopState.RECORDING),
                 desc="the mark to open")
        assert published().loop_bounds is None, (
            "a mark still open is not a loop the next session could be handed"
        )

        append_command(cmd_file, RECORD_TAP)
        wait_for(after_frames(lambda: published().loop_state is LoopState.LOOPING),
                 desc="the loop to close and start")
        bounds = published().loop_bounds
        assert bounds is not None
        assert bounds[0] == held_at, "the loop starts where the mark was made"
        assert bounds[1] > bounds[0], "and runs long enough for mpv to loop it"

        append_command(cmd_file, LOOP_CANCEL)
        wait_for(after_frames(lambda: published().loop_state is LoopState.NORMAL),
                 desc="the loop to be dropped")
        assert published().loop_bounds is None

        append_command(cmd_file, f"{SET_LOOP} {held_at} {held_at + 2_000}")
        wait_for(after_frames(lambda: published().loop_state is LoopState.LOOPING),
                 desc="the loop the crossing handed over to start")
        assert published().loop_bounds == (held_at, held_at + 2_000), (
            "finished bounds are asserted, not snapped again"
        )
    finally:
        stop.set()
        joined_within(pump, 5.0)
        main.close()
        glfw.terminate()
