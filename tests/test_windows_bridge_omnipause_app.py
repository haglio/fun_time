from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch

from fun_time.windows_bridge_omnipause_app import build_parser, main


def test_build_parser_accepts_omnipause_plan_arguments():
    args = build_parser().parse_args([
        "apply-leave",
        "--omni-paused",
        "1",
        "--robot-hand-mode-on",
        "0",
        "--skip-primary-resume",
        "1",
        "--plan-file",
        "plan.ini",
        "--portrait-port",
        "8091",
        "--landscape-port",
        "8092",
        "--primary-port",
        "8090",
        "--password",
        "pw",
    ])

    assert args.action == "apply-leave"
    assert args.skip_primary_resume == "1"


def test_main_writes_omnipause_plan_ini(tmp_path: Path):
    plan_file = tmp_path / "plan.ini"
    code = main([
        "toggle",
        "--omni-paused",
        "0",
        "--robot-hand-mode-on",
        "1",
        "--skip-primary-resume",
        "0",
        "--plan-file",
        str(plan_file),
    ])

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(plan_file, encoding="utf-8")

    assert code == 0
    assert parser.get("plan", "action") == "enter"
    assert parser.get("plan", "next_omni_paused") == "1"


def test_main_apply_enter_runs_non_window_omnipause_side_effects(tmp_path: Path):
    plan_file = tmp_path / "plan.ini"
    paused_file = tmp_path / "robot_hand_paused.txt"
    audio_file = tmp_path / "audio_paused.txt"

    with (
        patch("fun_time.windows_bridge_omnipause_app.vlc_http_cmd", return_value=True) as vlc_cmd,
        patch("fun_time.windows_bridge_omnipause_app.ensure_playback_state", return_value=True) as ensure_playback,
    ):
        code = main([
            "apply-enter",
            "--omni-paused",
            "0",
            "--robot-hand-mode-on",
            "0",
            "--skip-primary-resume",
            "0",
            "--plan-file",
            str(plan_file),
            "--portrait-port",
            "8091",
            "--landscape-port",
            "8092",
            "--primary-port",
            "8090",
            "--password",
            "pw",
            "--robot-hand-paused-file",
            str(paused_file),
            "--audio-paused-file",
            str(audio_file),
        ])

    assert code == 0
    assert vlc_cmd.call_count == 2
    ensure_playback.assert_called_once_with(8090, "pw", should_play=False)
    assert not paused_file.exists()
    assert not audio_file.exists()
