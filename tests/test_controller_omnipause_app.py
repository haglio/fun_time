from __future__ import annotations

import configparser
from pathlib import Path

from fun_time.controller_omnipause_app import build_parser, main


def test_build_parser_accepts_omnipause_plan_arguments():
    args = build_parser().parse_args([
        "leave",
        "--omni-paused",
        "1",
        "--robot-hand-mode-on",
        "0",
        "--skip-primary-resume",
        "1",
        "--plan-file",
        "plan.ini",
    ])

    assert args.action == "leave"
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
