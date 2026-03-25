from __future__ import annotations

from pathlib import Path

from fun_time.controller_dashboard_bridge import build_dashboard_snapshot_text, write_dashboard_snapshot


def test_build_dashboard_snapshot_text_matches_bridge_contract():
    text = build_dashboard_snapshot_text(
        f_mode_enabled=True,
        robot_link_enabled=False,
        osr2_mode="auto",
        mfp_alive=True,
        primary_uses_robot_hand=False,
        portrait_locked=True,
        landscape_locked=False,
    )

    assert text == (
        "[fmode]\n"
        "enabled=1\n"
        "[robot_link]\n"
        "enabled=0\n"
        "[osr2]\n"
        "mode=auto\n"
        "[mfp]\n"
        "alive=1\n"
        "[primary]\n"
        "uses_robot_hand=0\n"
        "locked=0\n"
        "[portrait]\n"
        "locked=1\n"
        "[landscape]\n"
        "locked=0\n"
    )


def test_write_dashboard_snapshot_writes_utf16_and_skips_identical_content(tmp_path: Path):
    output = tmp_path / "dashboard_state.ini"

    first = write_dashboard_snapshot(
        output,
        f_mode_enabled=False,
        robot_link_enabled=True,
        osr2_mode="controlled",
        mfp_alive=False,
        primary_uses_robot_hand=True,
        portrait_locked=False,
        landscape_locked=True,
    )
    second = write_dashboard_snapshot(
        output,
        f_mode_enabled=False,
        robot_link_enabled=True,
        osr2_mode="controlled",
        mfp_alive=False,
        primary_uses_robot_hand=True,
        portrait_locked=False,
        landscape_locked=True,
    )

    assert first is True
    assert second is False
    text = output.read_text(encoding="utf-16")
    assert "[primary]" in text
    assert "uses_robot_hand=1" in text
