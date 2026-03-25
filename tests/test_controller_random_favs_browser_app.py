from __future__ import annotations

import base64
import configparser
from pathlib import Path

from fun_time.controller_random_favs_browser_app import build_parser, main


def test_build_parser_accepts_random_favs_browser_plan_arguments():
    args = build_parser().parse_args([
        "write-plan",
        "--manifest-file",
        "browser_manifest.txt",
        "--shortcut-target",
        "chrome.exe",
        "--shortcut-work-dir",
        "C:\\Chrome",
        f"--shortcut-args-b64={base64.b64encode(b'--flag').decode('ascii')}",
        "--plan-file",
        "plan.ini",
    ])

    assert args.manifest_file == "browser_manifest.txt"
    assert args.shortcut_target == "chrome.exe"
    assert args.shortcut_args_b64 == base64.b64encode(b"--flag").decode("ascii")
    assert args.plan_file == "plan.ini"


def test_main_writes_random_favs_browser_launch_plan(tmp_path: Path):
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text(
        "Profile 2\nhttps://example.com/1\n",
        encoding="utf-8",
    )
    plan_file = tmp_path / "plan.ini"

    code = main([
        "write-plan",
        "--manifest-file",
        str(manifest_file),
        "--shortcut-target",
        "chrome.exe",
        "--shortcut-work-dir",
        "C:\\Chrome",
        "--shortcut-args-b64",
        "",
        "--plan-file",
        str(plan_file),
    ])

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(plan_file, encoding="utf-8")

    assert code == 0
    assert parser.get("plan", "should_launch") == "1"
    assert "chrome.exe" in parser.get("plan", "cmd")


def test_main_launch_returns_special_code_when_manifest_has_no_urls(tmp_path: Path):
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text("Profile 2\n", encoding="utf-8")

    code = main([
        "launch",
        "--manifest-file",
        str(manifest_file),
        "--shortcut-target",
        "chrome.exe",
        "--shortcut-work-dir",
        "C:\\Chrome",
        "--shortcut-args-b64",
        "",
    ])

    assert code == 3
