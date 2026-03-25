from __future__ import annotations

from pathlib import Path

from fun_time.dashboard_runtime import is_broker_heartbeat_fresh, load_dashboard_snapshot


def test_load_dashboard_snapshot_returns_none_when_missing(tmp_path: Path):
    assert load_dashboard_snapshot(tmp_path / "missing.ini") is None


def test_load_dashboard_snapshot_parses_controller_export(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[fmode]",
                "enabled=0",
                "[robot_link]",
                "enabled=1",
                "[osr2]",
                "mode=auto",
                "[mfp]",
                "alive=1",
                "[primary]",
                "responsive=1",
                "uses_robot_hand=0",
                "path=demo-primary.mp4",
                "locked=0",
                "[portrait]",
                "path=demo-portrait.mp4",
                "locked=1",
                "[landscape]",
                "path=demo-landscape.mp4",
                "locked=0",
                "[window]",
                "x=100",
                "y=200",
                "width=300",
                "height=400",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.mfp_alive is True
    assert snapshot.primary_responsive is True
    assert snapshot.osr2_mode == "auto"
    assert snapshot.primary_uses_robot_hand is False
    assert snapshot.primary.path == "demo-primary.mp4"
    assert snapshot.primary.locked is False
    assert snapshot.portrait.locked is True
    assert snapshot.landscape.path == "demo-landscape.mp4"
    assert snapshot.window.width == 300
    assert snapshot.window.height == 400


def test_load_dashboard_snapshot_supports_utf16_ahk_ini_exports(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state_utf16.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[fmode]",
                "enabled=1",
                "[robot_link]",
                "enabled=0",
                "[osr2]",
                "mode=auto",
                "[mfp]",
                "alive=1",
                "[primary]",
                "responsive=1",
                "uses_robot_hand=1",
                "path=primary.mp4",
                "locked=0",
                "[portrait]",
                "path=portrait.mp4",
                "locked=1",
                "[landscape]",
                "path=landscape.mp4",
                "locked=0",
                "[window]",
                "x=10",
                "y=20",
                "width=30",
                "height=40",
            ]
        ),
        encoding="utf-16",
    )

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.f_mode_enabled is True
    assert snapshot.robot_link_enabled is False
    assert snapshot.primary_uses_robot_hand is True
    assert snapshot.portrait.locked is True
    assert snapshot.window.x == 10


def test_load_dashboard_snapshot_supports_minimal_bridge_export(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state_minimal.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[fmode]",
                "enabled=1",
                "[robot_link]",
                "enabled=1",
                "[osr2]",
                "mode=controlled",
                "[mfp]",
                "alive=0",
                "[primary]",
                "uses_robot_hand=0",
                "locked=0",
                "[portrait]",
                "locked=1",
                "[landscape]",
                "locked=0",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.f_mode_enabled is True
    assert snapshot.robot_link_enabled is True
    assert snapshot.primary.path == ""
    assert snapshot.primary_responsive is False
    assert snapshot.portrait.locked is True
    assert snapshot.window.width == 0


def test_broker_heartbeat_is_fresh_when_recent(tmp_path: Path):
    heartbeat_file = tmp_path / "broker_heartbeat.txt"
    heartbeat_file.write_text("100.0", encoding="utf-8")

    assert is_broker_heartbeat_fresh(heartbeat_file, max_age_seconds=3.0, now=102.5) is True


def test_broker_heartbeat_is_stale_when_old_or_invalid(tmp_path: Path):
    stale_file = tmp_path / "stale_heartbeat.txt"
    stale_file.write_text("100.0", encoding="utf-8")
    invalid_file = tmp_path / "invalid_heartbeat.txt"
    invalid_file.write_text("not-a-float", encoding="utf-8")

    assert is_broker_heartbeat_fresh(stale_file, max_age_seconds=3.0, now=104.0) is False
    assert is_broker_heartbeat_fresh(invalid_file, now=101.0) is False
    assert is_broker_heartbeat_fresh(tmp_path / "missing.txt", now=101.0) is False
