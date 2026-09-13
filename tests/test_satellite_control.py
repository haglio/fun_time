from __future__ import annotations

from fun_time.satellite_control import SatelliteStatus, read_satellite_status


class TestReadSatelliteStatus:
    def test_parses_the_status_fields(self, tmp_path):
        status = tmp_path / "portrait_status.txt"
        status.write_text(
            "video=C:/clips/a.mp4\nposition_ms=1500\nduration_ms=5000\npaused=0\nlocked=1\n"
            "playlist_length=3\n",
            encoding="utf-8",
        )

        s = read_satellite_status(status)

        assert s == SatelliteStatus(
            video="C:/clips/a.mp4",
            position_ms=1500,
            duration_ms=5000,
            paused=False,
            locked=True,
            playlist_length=3,
        )

    def test_missing_file_is_an_empty_status(self, tmp_path):
        s = read_satellite_status(tmp_path / "nope.txt")
        assert s.video == "" and s.duration_ms == 0 and s.paused is False

    def test_fraction_is_position_over_duration(self, tmp_path):
        status = tmp_path / "s.txt"
        status.write_text("video=v\nposition_ms=2000\nduration_ms=8000\npaused=0\nlocked=0\n", encoding="utf-8")
        assert read_satellite_status(status).fraction == 0.25

    def test_fraction_is_none_without_a_known_duration(self, tmp_path):
        status = tmp_path / "s.txt"
        status.write_text("video=v\nposition_ms=0\nduration_ms=0\npaused=0\nlocked=0\n", encoding="utf-8")
        assert read_satellite_status(status).fraction is None
