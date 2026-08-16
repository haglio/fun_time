from __future__ import annotations

from pathlib import Path

from fun_time.origenerator_control import read_origenerator_status


def test_absent_file_reads_as_nothing_showing(tmp_path: Path):
    status = read_origenerator_status(tmp_path / "gone.txt")
    assert status.portrait_active is False
    assert status.landscape_active is False


def test_occupancy_parses_per_side_and_extra_fields_ride_through(tmp_path: Path):
    # The file carries _video/_locked too; the session consumes occupancy alone,
    # so the rest must be tolerated, never tripped over.
    status_file = tmp_path / "origenerator_status.txt"
    status_file.write_text(
        "portrait_active=1\n"
        "portrait_video=C:\\made\\up\\tall.png\n"
        "portrait_locked=1\n"
        "landscape_active=0\n"
        "landscape_video=\n"
        "landscape_locked=0\n",
        encoding="utf-8",
    )
    status = read_origenerator_status(status_file)
    assert status.portrait_active is True
    assert status.landscape_active is False


def test_side_active_answers_by_name(tmp_path: Path):
    status_file = tmp_path / "origenerator_status.txt"
    status_file.write_text("portrait_active=1\nlandscape_active=0\n", encoding="utf-8")
    status = read_origenerator_status(status_file)
    assert status.side_active("portrait") is True
    assert status.side_active("landscape") is False
