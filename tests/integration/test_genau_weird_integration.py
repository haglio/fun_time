from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from app_support.flick_folder import flat_flicks_in, flicks_folder_in, weird_pile_in

from fun_time.funestra_status import read_genau_status
from fun_time.lock import MARKED_WEIRD
from tests.made_up_genau import made_up_genau, point_the_shared_settings_at

from .integration_support import (
    FunTimeIntegrationSession,
    build_integration_config,
    build_integration_temp_root,
    clip_of_a_test_pattern,
    let_seconds_pass,
)

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]

GENAU_TAKES_A_VERB_WITHIN_S = 2.0
GENAUS_KEYS = ("genau_next_flick", "genau_prev_flick", "genau_lock", "genau_weird_flick")


def _condemned(weird: Path) -> list[Path]:
    return [path for path in weird.rglob("*") if path.is_file()] if weird.is_dir() else []


def _genaus_flick(session: FunTimeIntegrationSession) -> str:
    return read_genau_status(session.config.genau_status_file).flick


def test_genaus_keys_do_nothing_with_video_on_the_main_funestra_and_mark_weird_says_so_in_genau_mode(
        monkeypatch):
    temp_root = build_integration_temp_root()
    genau = made_up_genau(temp_root)
    point_the_shared_settings_at(monkeypatch, genau)
    played_from = flat_flicks_in(flicks_folder_in(genau)) / "non_AI"
    played_from.mkdir()
    for name in ("alpha one.mp4", "beta two.mp4", "gamma three.mp4"):
        clip_of_a_test_pattern(played_from / name, size="640x360", seconds=2)
    weird = weird_pile_in(genau)
    session = FunTimeIntegrationSession(build_integration_config(temp_root))
    try:
        session.start()
        session.wait_until(lambda: _genaus_flick(session), timeout=15,
                           description="Genau publishing the flick it opened on")
        flick_before = _genaus_flick(session)
        for command in GENAUS_KEYS:
            session.write_dashboard_command(command)
        let_seconds_pass(GENAU_TAKES_A_VERB_WITHIN_S)

        genau = read_genau_status(session.config.genau_status_file)
        assert (genau.flick, genau.locked, _condemned(weird)) == (flick_before, True, [])

        session.write_dashboard_command("genau_activate")
        session.wait_for_new_log("Switched to genau mode", timeout=12)
        session.write_dashboard_command("genau_weird_flick")

        session.wait_until(
            lambda: _condemned(weird) and any(
                n.message == MARKED_WEIRD and n.source == "main" for n in session.notices()),
            timeout=15, description='the flick on screen marked weird, and "Marked weird" flashed')
        let_seconds_pass(GENAU_TAKES_A_VERB_WITHIN_S)

        assert len(_condemned(weird)) == 1
    finally:
        session.stop()
