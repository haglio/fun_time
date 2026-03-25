from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fun_time.controller_dashboard_bridge_app import build_parser, main


def test_build_parser_accepts_dashboard_bridge_arguments():
    args = build_parser().parse_args([
        "--output-file",
        "dashboard_state.ini",
        "--f-mode-enabled",
        "1",
        "--robot-link-enabled",
        "0",
        "--osr2-mode",
        "auto",
        "--mfp-alive",
        "1",
        "--primary-uses-robot-hand",
        "0",
        "--portrait-locked",
        "1",
        "--landscape-locked",
        "0",
    ])

    assert args.output_file == "dashboard_state.ini"
    assert args.osr2_mode == "auto"


def test_main_dispatches_dashboard_snapshot_write(tmp_path: Path):
    output = tmp_path / "dashboard_state.ini"

    with patch("fun_time.controller_dashboard_bridge_app.write_dashboard_snapshot", return_value=True) as write_snapshot:
        code = main([
            "--output-file",
            str(output),
            "--f-mode-enabled",
            "1",
            "--robot-link-enabled",
            "1",
            "--osr2-mode",
            "controlled",
            "--mfp-alive",
            "0",
            "--primary-uses-robot-hand",
            "1",
            "--portrait-locked",
            "0",
            "--landscape-locked",
            "1",
        ])

    assert code == 0
    write_snapshot.assert_called_once_with(
        str(output),
        f_mode_enabled=True,
        robot_link_enabled=True,
        osr2_mode="controlled",
        mfp_alive=False,
        primary_uses_robot_hand=True,
        portrait_locked=False,
        landscape_locked=True,
    )
