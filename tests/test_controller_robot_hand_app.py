from __future__ import annotations

import configparser
from pathlib import Path

from fun_time.controller_robot_hand_app import build_parser, main


def test_build_parser_accepts_robot_hand_plan_arguments():
    args = build_parser().parse_args([
        "toggle-enabled",
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
    ])

    assert args.action == "toggle-enabled"
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
