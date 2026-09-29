from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time.lock import MARKED_WEIRD
from fun_time.player_status import read_genau_status

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
)

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]

GENAU_TAKES_A_VERB_WITHIN_S = 2.0
GENAUS_KEYS = ("genau_next_clip", "genau_prev_clip", "genau_lock", "genau_weird_clip")


def _test_pattern(folder: Path, name: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is what makes this test's Genau clips, and it is not on PATH")
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
         "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(folder / name)],
        check=True, timeout=60, **hidden_subprocess_kwargs())


def _session_whose_genau_plays(clips: Path) -> FunTimeIntegrationSession:
    config_path = build_integration_config(clips.parent.parent)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["paths"]["clips_dir"] = str(clips)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return FunTimeIntegrationSession(config_path)


def _condemned(weird: Path) -> list[Path]:
    return list(weird.iterdir()) if weird.is_dir() else []


def _genaus_clip(session: FunTimeIntegrationSession) -> str:
    return read_genau_status(session.config.genau_status_file).clip


def test_genaus_keys_do_nothing_with_video_on_the_main_player_and_mark_weird_says_so_in_genau_mode():
    clips = build_integration_temp_root() / "genau" / "clips"
    clips.mkdir(parents=True)
    for name in ("alpha one.mp4", "beta two.mp4", "gamma three.mp4"):
        _test_pattern(clips, name)
    weird = clips.parent / "weird"
    session = _session_whose_genau_plays(clips)
    try:
        session.start()
        session.wait_until(lambda: _genaus_clip(session), timeout=15,
                           description="Genau publishing the clip it opened on")
        clip_before = _genaus_clip(session)
        for command in GENAUS_KEYS:
            session.write_dashboard_command(command)
        time.sleep(GENAU_TAKES_A_VERB_WITHIN_S)

        genau = read_genau_status(session.config.genau_status_file)
        assert (genau.clip, genau.locked, _condemned(weird)) == (clip_before, True, [])

        session.write_dashboard_command("genau_activate")
        session.wait_for_new_log("Switched to genau mode", timeout=12)
        session.write_dashboard_command("genau_weird_clip")

        session.wait_until(
            lambda: _condemned(weird) and any(
                n.message == MARKED_WEIRD and n.source == "main" for n in session.notices()),
            timeout=15, description='the clip on screen marked weird, and "Marked weird" flashed')
        time.sleep(GENAU_TAKES_A_VERB_WITHIN_S)

        assert len(_condemned(weird)) == 1
    finally:
        session.stop()
