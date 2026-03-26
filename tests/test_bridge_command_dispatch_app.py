from __future__ import annotations

import configparser
from pathlib import Path

from fun_time.bridge_command_dispatch_app import main
from fun_time.windows_bridge_dispatch_loop import read_shared_state


def test_dispatch_app_writes_result_for_unknown_command(cfg_path: Path, tmp_path: Path):
    result_file = tmp_path / "result.ini"

    exit_code = main([
        "bogus_command",
        "--result-file", str(result_file),
        "--config-path", str(cfg_path),
        "--vlc-password", "pw",
    ])

    assert exit_code == 0
    assert result_file.exists()
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.get("state", "locked2") == "0"
    assert parser.get("state", "omni_paused") == "0"
    assert parser.getint("ops", "count") == 0


def test_dispatch_app_passes_state_through(cfg_path: Path, tmp_path: Path):
    result_file = tmp_path / "result.ini"

    exit_code = main([
        "bogus_command",
        "--result-file", str(result_file),
        "--config-path", str(cfg_path),
        "--vlc-password", "pw",
        "--locked2", "1",
        "--f-mode-enabled", "1",
    ])

    assert exit_code == 0
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.get("state", "locked2") == "1"
    assert parser.get("state", "f_mode_enabled") == "1"


def test_dispatch_app_writes_dashboard_snapshot_when_enabled(cfg_path: Path, tmp_path: Path):
    result_file = tmp_path / "result.ini"
    dashboard_file = tmp_path / "dashboard_state.ini"
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    (state_dir / "robot_hand_enabled.txt").write_text("1", encoding="utf-8")
    (state_dir / "robot_hand_mode.txt").write_text("1", encoding="utf-8")

    exit_code = main([
        "bogus_command",
        "--result-file", str(result_file),
        "--config-path", str(cfg_path),
        "--vlc-password", "pw",
        "--locked2", "1",
        "--f-mode-enabled", "1",
        "--dashboard-state-file", str(dashboard_file),
        "--dashboard-enabled", "1",
        "--mfp-alive", "1",
    ])

    assert exit_code == 0
    assert dashboard_file.exists()
    text = dashboard_file.read_text(encoding="utf-16")
    assert "[fmode]" in text
    assert "enabled=1" in text
    assert "[portrait]" in text
    assert "locked=1" in text


def test_dispatch_app_skips_dashboard_when_disabled(cfg_path: Path, tmp_path: Path):
    result_file = tmp_path / "result.ini"
    dashboard_file = tmp_path / "dashboard_state.ini"

    exit_code = main([
        "bogus_command",
        "--result-file", str(result_file),
        "--config-path", str(cfg_path),
        "--vlc-password", "pw",
        "--dashboard-state-file", str(dashboard_file),
        "--dashboard-enabled", "0",
    ])

    assert exit_code == 0
    assert not dashboard_file.exists()


def test_dispatch_app_writes_shared_state_file(cfg_path: Path, tmp_path: Path):
    result_file = tmp_path / "result.ini"
    shared_file = tmp_path / "shared_bridge_state.ini"

    exit_code = main([
        "bogus_command",
        "--result-file", str(result_file),
        "--config-path", str(cfg_path),
        "--vlc-password", "pw",
        "--locked2", "1",
        "--omni-paused", "1",
        "--shared-state-file", str(shared_file),
    ])

    assert exit_code == 0
    assert shared_file.exists()
    state = read_shared_state(shared_file)
    assert state is not None
    assert state.locked2 is True
    assert state.omni_paused is True


def test_dispatch_app_quarter_button_writes_robot_hand_cmd(cfg_path: Path, tmp_path: Path):
    result_file = tmp_path / "result.ini"
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)

    exit_code = main([
        "quarter_button",
        "--result-file", str(result_file),
        "--config-path", str(cfg_path),
        "--vlc-password", "pw",
    ])

    assert exit_code == 0
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(result_file, encoding="utf-8")
    assert parser.getint("ops", "count") == 0
