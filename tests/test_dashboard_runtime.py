from __future__ import annotations

from pathlib import Path

from fun_time.dashboard_runtime import (
    GenauStatus,
    NauStatus,
    is_broker_heartbeat_fresh,
    is_osr2_device_on,
    load_dashboard_snapshot,
    read_genau_status,
    read_nau_status,
)


def test_load_dashboard_snapshot_returns_none_when_missing(tmp_path: Path):
    assert load_dashboard_snapshot(tmp_path / "missing.ini") is None


def test_load_dashboard_snapshot_parses_controller_export(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[fmode]",
                "enabled=0",
                "[genau_link]",
                "enabled=1",
                "[osr2]",
                "mode=auto",
                "[primary]",
                "responsive=1",
                "uses_genau=0",
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
    assert snapshot.primary_responsive is True
    assert snapshot.osr2_mode == "auto"
    assert snapshot.primary_mode == "nau"
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
                "[genau_link]",
                "enabled=0",
                "[osr2]",
                "mode=auto",
                "[primary]",
                "responsive=1",
                "uses_genau=1",
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
    assert snapshot.primary_mode == "genau"
    assert snapshot.portrait.locked is True
    assert snapshot.window.x == 10


def test_load_dashboard_snapshot_supports_minimal_bridge_export(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state_minimal.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[fmode]",
                "enabled=1",
                "[genau_link]",
                "enabled=1",
                "[osr2]",
                "mode=controlled",
                "[primary]",
                "uses_genau=0",
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
    assert snapshot.f_mode_enabled is True
    assert snapshot.primary.path == ""
    assert snapshot.primary_responsive is False
    assert snapshot.portrait.locked is True
    assert snapshot.window.width == 0


def test_load_dashboard_snapshot_reads_omnipause_state(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[fmode]",
                "enabled=0",
                "[genau_link]",
                "enabled=1",
                "[osr2]",
                "mode=auto",
                "[omnipause]",
                "active=1",
                "[primary]",
                "uses_genau=0",
                "locked=0",
                "[portrait]",
                "locked=0",
                "[landscape]",
                "locked=0",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.omni_paused is True


def test_load_dashboard_snapshot_defaults_omnipause_to_false(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[fmode]",
                "enabled=0",
                "[genau_link]",
                "enabled=1",
                "[osr2]",
                "mode=auto",
                "[primary]",
                "uses_genau=0",
                "locked=0",
                "[portrait]",
                "locked=0",
                "[landscape]",
                "locked=0",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.omni_paused is False


def test_load_dashboard_snapshot_reads_voice_active(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[fmode]",
                "enabled=0",
                "[osr2]",
                "mode=controlled",
                "[omnipause]",
                "active=0",
                "[voice]",
                "active=0",
                "[primary]",
                "uses_genau=0",
                "locked=0",
                "[portrait]",
                "locked=0",
                "[landscape]",
                "locked=0",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.voice_active is False


def test_load_dashboard_snapshot_defaults_voice_active_to_true(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[fmode]",
                "enabled=0",
                "[osr2]",
                "mode=controlled",
                "[primary]",
                "uses_genau=0",
                "locked=0",
                "[portrait]",
                "locked=0",
                "[landscape]",
                "locked=0",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.voice_active is True


def test_osr2_device_on_when_rx_recent(tmp_path: Path):
    rx_file = tmp_path / "osr2_serial_rx.txt"
    rx_file.write_text("100.0", encoding="utf-8")

    assert is_osr2_device_on(rx_file, now=115.0) is True


def test_osr2_device_off_when_rx_stale(tmp_path: Path):
    rx_file = tmp_path / "osr2_serial_rx.txt"
    rx_file.write_text("100.0", encoding="utf-8")

    assert is_osr2_device_on(rx_file, now=117.0) is False


def test_osr2_device_off_when_rx_missing(tmp_path: Path):
    assert is_osr2_device_on(tmp_path / "missing.txt", now=100.0) is False


def test_osr2_device_off_when_rx_invalid(tmp_path: Path):
    rx_file = tmp_path / "osr2_serial_rx.txt"
    rx_file.write_text("not-a-float", encoding="utf-8")

    assert is_osr2_device_on(rx_file, now=100.0) is False


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


def test_read_genau_status_returns_defaults_when_missing(tmp_path: Path):
    status = read_genau_status(tmp_path / "missing.txt")

    assert status == GenauStatus()
    assert status.cruise_active is False
    assert status.shape == "sine"


def test_read_genau_status_parses_active_cruise_and_shape(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("cruise=1\nshape=triangle\n", encoding="utf-8")

    status = read_genau_status(status_file)

    assert status.cruise_active is True
    assert status.shape == "triangle"


def test_read_genau_status_handles_inactive_cruise(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("cruise=0\nshape=sawtooth\n", encoding="utf-8")

    status = read_genau_status(status_file)

    assert status.cruise_active is False
    assert status.shape == "sawtooth"


def test_read_genau_status_parses_limit_flags(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text(
        "cruise=0\nshape=sine\n"
        "amp_at_max=1\namp_at_min=0\n"
        "ctr_at_max=0\nctr_at_min=1\n"
        "spd_at_max=0\nspd_at_min=0\n",
        encoding="utf-8",
    )

    status = read_genau_status(status_file)

    assert status.amp_at_max is True
    assert status.amp_at_min is False
    assert status.ctr_at_max is False
    assert status.ctr_at_min is True
    assert status.spd_at_max is False
    assert status.spd_at_min is False


def test_read_nau_status_parses_has_funscript(tmp_path: Path):
    # Nau publishes has_funscript per current video; the hybrid handoff arbiter
    # reads it to decide whether the funscript or Genau drives the OSR2.
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nposition_ms=567\nhas_funscript=1\nstate=normal\npaused=0\n",
        encoding="utf-8",
    )

    status = read_nau_status(status_file)

    assert status.has_funscript is True


def test_read_nau_status_defaults_has_funscript_to_false(tmp_path: Path):
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nhas_funscript=0\n", encoding="utf-8")

    assert read_nau_status(status_file).has_funscript is False
    assert read_nau_status(tmp_path / "missing.txt").has_funscript is False


