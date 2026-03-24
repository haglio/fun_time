from __future__ import annotations

from pathlib import Path

from fun_time.dashboard_runtime import load_dashboard_snapshot


def test_load_dashboard_snapshot_returns_none_when_missing(tmp_path: Path):
    assert load_dashboard_snapshot(tmp_path / "missing.ini") is None


def test_load_dashboard_snapshot_parses_controller_export(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[broker]",
                "running=1",
                "[controller]",
                "running=1",
                "[fmode]",
                "enabled=0",
                "[robot_link]",
                "enabled=1",
                "[osr2]",
                "mode=auto",
                "[mfp]",
                "connected=1",
                "[primary]",
                "label=Non-AI VLC",
                "clip=demo-primary.mp4",
                "highlight=1",
                "accent=osr2",
                "[portrait]",
                "label=Portrait AI VLC",
                "clip=demo-portrait.mp4",
                "highlight=0",
                "[landscape]",
                "label=Landscape AI VLC",
                "clip=demo-landscape.mp4",
                "highlight=1",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.broker_running is True
    assert snapshot.osr2_mode == "auto"
    assert snapshot.primary.label == "Non-AI VLC"
    assert snapshot.primary.highlight is True
    assert snapshot.landscape.clip == "demo-landscape.mp4"
