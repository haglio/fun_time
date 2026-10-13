"""The headset's main picture is painted at a 60fps video's own rate: every
frame the engine decodes reaches the texture, not every other one."""
from __future__ import annotations

import os
import sys
import time

import glfw
import pytest

from fun_time_vr.funestras import main_video
from fun_time_vr.gl_contexts import SharedContexts, hidden_gl_window
from fun_time_vr.render import RenderTarget
from fun_time_vr.scheduling import ahead_of_background_work

from .integration_support import build_integration_temp_root, clip_of_a_test_pattern, wait_for
from .run_clock import Budget, OnTimeClock

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]

VIDEO_FPS = 60
VIDEO_SECONDS = 10
MEASURED_S = 5.0
HEADSET_HZ = 72.0
PAINTED_PER_S_AT_LEAST = 45
MEASURING_GIVES_UP_AFTER_S = 60.0


def _pictures_shown(video, target: RenderTarget, *, seconds: float) -> tuple[int, float, float]:
    shown = 0
    period = 1.0 / HEADSET_HZ
    began = time.monotonic()
    gives_up = Budget(MEASURING_GIVES_UP_AFTER_S)
    with OnTimeClock(slack_s=1.0 / VIDEO_FPS) as on_time:
        while on_time.seconds() < seconds and not gives_up.expired():
            started = time.perf_counter()
            if video.show_newest(target) and not on_time.held():
                shown += 1
            time.sleep(max(0.0, period - (time.perf_counter() - started)))
        return shown, on_time.seconds(), time.monotonic() - began


def test_the_main_picture_is_painted_at_a_sixty_fps_videos_own_rate():
    pattern = clip_of_a_test_pattern(build_integration_temp_root() / "sixty_fps_test_pattern.mp4",
                                     size="1280x720", seconds=VIDEO_SECONDS, rate=VIDEO_FPS)
    assert glfw.init(), "glfw failed to initialize"
    window = hidden_gl_window("vr-paint-rate-test")
    glfw.make_context_current(window)
    video = main_video(SharedContexts(window))
    target = RenderTarget()
    try:
        video.engine.set_loop_file(True)
        video.engine.load(pattern)
        wait_for(lambda: video.show_newest(target), desc="the video's first painted frame")
        with ahead_of_background_work():
            shown, on_time_s, wall_s = _pictures_shown(video, target, seconds=MEASURED_S)
    finally:
        video.close()
        target.close()
        glfw.destroy_window(window)
        glfw.terminate()
    assert on_time_s >= MEASURED_S, (
        f"in {wall_s:.0f}s the run had the processor on time for only {on_time_s:.1f}s, "
        "so the picture's rate was never measured")
    per_second = shown / on_time_s
    assert per_second >= PAINTED_PER_S_AT_LEAST, (
        f"the main picture was painted {per_second:.0f} times a second for a {VIDEO_FPS}fps "
        f"video: {shown} pictures in the {on_time_s:.1f}s of {wall_s:.1f}s the run had the "
        "processor on time")
