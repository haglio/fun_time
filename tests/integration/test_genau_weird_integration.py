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

from fun_time.funestra_status import read_genau_status
from fun_time.lock import MARKED_WEIRD

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
GENAUS_KEYS = ("genau_next_flick", "genau_prev_flick", "genau_lock", "genau_weird_flick")


def _test_pattern(folder: Path, name: str) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is what makes this test's Genau flicks, and it is not on PATH")
    subprocess.run(
        [ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
         "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(folder / name)],
        check=True, timeout=60, **hidden_subprocess_kwargs())


def _session_whose_genau_plays(flicks: Path) -> FunTimeIntegrationSession:
    config_path = build_integration_config(flicks.parent.parent)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["paths"]["flicks_dir"] = str(flicks)
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return FunTimeIntegrationSession(config_path)


def _condemned(weird: Path) -> list[Path]:
    return [path for path in weird.rglob("*") if path.is_file()] if weird.is_dir() else []


def _genaus_flick(session: FunTimeIntegrationSession) -> str:
    return read_genau_status(session.config.genau_status_file).flick


def test_genaus_keys_do_nothing_with_video_on_the_main_funestra_and_mark_weird_says_so_in_genau_mode():
    flicks = build_integration_temp_root() / "genau" / "flicks"
    (flicks / "2D" / "non_AI").mkdir(parents=True)
    for name in ("alpha one.mp4", "beta two.mp4", "gamma three.mp4"):
        _test_pattern(flicks / "2D" / "non_AI", name)
    weird = flicks.parent / "weird"
    session = _session_whose_genau_plays(flicks)
    try:
        session.start()
        session.wait_until(lambda: _genaus_flick(session), timeout=15,
                           description="Genau publishing the flick it opened on")
        flick_before = _genaus_flick(session)
        for command in GENAUS_KEYS:
            session.write_dashboard_command(command)
        time.sleep(GENAU_TAKES_A_VERB_WITHIN_S)

        genau = read_genau_status(session.config.genau_status_file)
        assert (genau.flick, genau.locked, _condemned(weird)) == (flick_before, True, [])

        session.write_dashboard_command("genau_activate")
        session.wait_for_new_log("Switched to genau mode", timeout=12)
        session.write_dashboard_command("genau_weird_flick")

        session.wait_until(
            lambda: _condemned(weird) and any(
                n.message == MARKED_WEIRD and n.source == "main" for n in session.notices()),
            timeout=15, description='the flick on screen marked weird, and "Marked weird" flashed')
        time.sleep(GENAU_TAKES_A_VERB_WITHIN_S)

        assert len(_condemned(weird)) == 1
    finally:
        session.stop()
