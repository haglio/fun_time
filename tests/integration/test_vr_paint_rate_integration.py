"""The headset's main picture is painted at a 60fps video's own rate: every
frame the engine decodes reaches the texture, not every other one."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import glfw
import pytest
from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time_vr.gl_contexts import SharedContexts, hidden_gl_window
from fun_time_vr.player import main_video
from fun_time_vr.render import RenderTarget
from fun_time_vr.scheduling import ahead_of_background_work

from .integration_support import build_integration_temp_root, wait_for

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]

CLIP_FPS = 60
CLIP_SECONDS = 10
MEASURED_S = 5.0
HEADSET_HZ = 72.0
PAINTED_PER_S_AT_LEAST = 45


def _sixty_fps_test_pattern(folder: Path) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is what makes this test's clip, and it is not on PATH")
    clip = folder / "sixty_fps_test_pattern.mp4"
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=size=1280x720:rate={CLIP_FPS}",
         "-t", str(CLIP_SECONDS), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True, timeout=60, **hidden_subprocess_kwargs())
    return clip


def _pictures_shown(video, target: RenderTarget, *, seconds: float) -> int:
    shown = 0
    period = 1.0 / HEADSET_HZ
    ends = time.perf_counter() + seconds
    while (started := time.perf_counter()) < ends:
        if video.show_newest(target):
            shown += 1
        time.sleep(max(0.0, period - (time.perf_counter() - started)))
    return shown


def test_the_main_picture_is_painted_at_a_sixty_fps_videos_own_rate():
    clip = _sixty_fps_test_pattern(build_integration_temp_root())
    assert glfw.init(), "glfw failed to initialize"
    window = hidden_gl_window("vr-paint-rate-test")
    glfw.make_context_current(window)
    video = main_video(SharedContexts(window))
    target = RenderTarget()
    try:
        video.player.load(clip)
        wait_for(lambda: video.show_newest(target), desc="the clip's first painted frame")
        with ahead_of_background_work():
            shown = _pictures_shown(video, target, seconds=MEASURED_S)
    finally:
        video.close()
        target.close()
        glfw.destroy_window(window)
        glfw.terminate()
    per_second = shown / MEASURED_S
    assert per_second >= PAINTED_PER_S_AT_LEAST, (
        f"the main picture was painted {per_second:.0f} times a second "
        f"for a {CLIP_FPS}fps video")
