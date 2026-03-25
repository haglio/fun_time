from __future__ import annotations

import configparser
from pathlib import Path

from fun_time.controller_lock_app import build_parser, main


def test_build_parser_accepts_lock_plan_arguments():
    args = build_parser().parse_args([
        "toggle-lock",
        "--which",
        "2",
        "--locked",
        "0",
        "--current-path",
        "clip.mp4",
        "--plan-file",
        "plan.ini",
    ])

    assert args.action == "toggle-lock"
    assert args.which == 2
    assert args.locked == "0"


def test_main_writes_plan_ini(tmp_path: Path):
    plan_file = tmp_path / "plan.ini"

    code = main([
        "discard",
        "--which",
        "3",
        "--locked",
        "1",
        "--current-path",
        "odd.mp4",
        "--plan-file",
        str(plan_file),
    ])

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(plan_file, encoding="utf-8")

    assert code == 0
    assert parser.get("plan", "next_locked") == "0"
    assert parser.get("plan", "move_to_weird") == "1"
