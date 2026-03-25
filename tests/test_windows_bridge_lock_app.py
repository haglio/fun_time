from __future__ import annotations

import configparser
from pathlib import Path

import fun_time.windows_bridge_lock_app as controller_lock_app


def test_build_parser_accepts_lock_plan_arguments():
    args = controller_lock_app.build_parser().parse_args([
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

    code = controller_lock_app.main([
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


def test_apply_toggle_lock_runs_repeat_and_favs_side_effects(monkeypatch, tmp_path: Path):
    plan_file = tmp_path / "plan.ini"
    calls: list[tuple] = []

    monkeypatch.setattr(controller_lock_app, "set_repeat_mode", lambda port, password, target: calls.append(("repeat", port, password, target)) or True)
    monkeypatch.setattr(controller_lock_app, "ensure_in_favs", lambda favs_file, path: calls.append(("favs", favs_file, path)))

    code = controller_lock_app.main([
        "apply-toggle-lock",
        "--which",
        "2",
        "--locked",
        "0",
        "--current-path",
        "clip.mp4",
        "--plan-file",
        str(plan_file),
        "--port",
        "8091",
        "--password",
        "pw",
        "--favs-file",
        str(tmp_path / "favs.csv"),
    ])

    assert code == 0
    assert calls == [
        ("repeat", 8091, "pw", "one"),
        ("favs", tmp_path / "favs.csv", "clip.mp4"),
    ]


def test_apply_discard_runs_remove_advance_and_weird_side_effects(monkeypatch, tmp_path: Path):
    plan_file = tmp_path / "plan.ini"
    calls: list[tuple] = []

    monkeypatch.setattr(controller_lock_app, "set_repeat_mode", lambda port, password, target: calls.append(("repeat", port, password, target)) or True)
    monkeypatch.setattr(controller_lock_app, "remove_from_favs", lambda favs_file, path: calls.append(("remove", favs_file, path)))
    monkeypatch.setattr(controller_lock_app, "vlc_http_cmd", lambda port, command, password: calls.append(("cmd", port, command, password)) or True)
    monkeypatch.setattr(controller_lock_app, "move_to_weird", lambda weird_dir, source: calls.append(("weird", weird_dir, source)))

    code = controller_lock_app.main([
        "apply-discard",
        "--which",
        "3",
        "--locked",
        "1",
        "--current-path",
        "odd.mp4",
        "--plan-file",
        str(plan_file),
        "--port",
        "8092",
        "--password",
        "pw",
        "--favs-file",
        str(tmp_path / "favs.csv"),
        "--weird-dir",
        str(tmp_path / "weird"),
    ])

    assert code == 0
    assert calls == [
        ("repeat", 8092, "pw", "all"),
        ("remove", tmp_path / "favs.csv", "odd.mp4"),
        ("cmd", 8092, "pl_next", "pw"),
        ("weird", tmp_path / "weird", Path("odd.mp4")),
    ]
