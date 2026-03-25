from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch

from fun_time.controller_robot_hand_app import build_parser, main


def test_build_parser_accepts_robot_hand_plan_arguments():
    args = build_parser().parse_args([
        "apply-sync-state",
        "--robot-hand-mode-on",
        "0",
        "--enabled",
        "1",
        "--mode-state-on",
        "1",
        "--omni-paused",
        "0",
        "--plan-file",
        "plan.ini",
        "--enabled-file",
        "enabled.txt",
        "--paused-file",
        "paused.txt",
        "--audio-paused-file",
        "audio.txt",
        "--primary-port",
        "8090",
        "--password",
        "pw",
    ])

    assert args.action == "apply-sync-state"
    assert args.enabled == "1"


def test_main_writes_robot_hand_plan_ini(tmp_path: Path):
    plan_file = tmp_path / "plan.ini"
    code = main([
        "sync-state",
        "--robot-hand-mode-on",
        "0",
        "--enabled",
        "1",
        "--mode-state-on",
        "1",
        "--omni-paused",
        "0",
        "--plan-file",
        str(plan_file),
    ])

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(plan_file, encoding="utf-8")

    assert code == 0
    assert parser.get("plan", "next_robot_hand_mode") == "1"
    assert parser.get("plan", "enforce_outputs") == "1"


def test_main_apply_toggle_enabled_writes_state_files_and_primary_playback(tmp_path: Path):
    plan_file = tmp_path / "plan.ini"
    enabled_file = tmp_path / "enabled.txt"
    paused_file = tmp_path / "paused.txt"
    audio_file = tmp_path / "audio.txt"

    with patch("fun_time.controller_robot_hand_app.ensure_playback_state", return_value=True) as ensure_playback:
        code = main([
            "apply-toggle-enabled",
            "--robot-hand-mode-on",
            "0",
            "--enabled",
            "1",
            "--mode-state-on",
            "1",
            "--omni-paused",
            "0",
            "--plan-file",
            str(plan_file),
            "--enabled-file",
            str(enabled_file),
            "--paused-file",
            str(paused_file),
            "--audio-paused-file",
            str(audio_file),
            "--primary-port",
            "8090",
            "--password",
            "pw",
        ])

    assert code == 0
    assert enabled_file.read_text(encoding="utf-8") == "0"
    assert paused_file.read_text(encoding="utf-8") == "1"
    assert audio_file.read_text(encoding="utf-8") == "1"
    ensure_playback.assert_called_once_with(8090, "pw", should_play=True)


def test_main_apply_sync_state_writes_pause_files_and_primary_playback(tmp_path: Path):
    plan_file = tmp_path / "plan.ini"
    enabled_file = tmp_path / "enabled.txt"
    paused_file = tmp_path / "paused.txt"
    audio_file = tmp_path / "audio.txt"

    with patch("fun_time.controller_robot_hand_app.ensure_playback_state", return_value=True) as ensure_playback:
        code = main([
            "apply-sync-state",
            "--robot-hand-mode-on",
            "0",
            "--enabled",
            "1",
            "--mode-state-on",
            "1",
            "--omni-paused",
            "0",
            "--plan-file",
            str(plan_file),
            "--enabled-file",
            str(enabled_file),
            "--paused-file",
            str(paused_file),
            "--audio-paused-file",
            str(audio_file),
            "--primary-port",
            "8090",
            "--password",
            "pw",
        ])

    assert code == 0
    assert not enabled_file.exists()
    assert paused_file.read_text(encoding="utf-8") == "0"
    assert audio_file.read_text(encoding="utf-8") == "0"
    ensure_playback.assert_called_once_with(8090, "pw", should_play=False)
