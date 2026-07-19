from __future__ import annotations

from pathlib import Path

from fun_time.child_log import open_child_log


class TestOpenChildLog:
    def test_banner_names_the_launch_and_its_argv(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"

        with open_child_log(log_file, ["pythonw.exe", "-m", "satellite", "--title", "Portrait"]):
            pass

        banner = log_file.read_text(encoding="utf-8")
        assert "pythonw.exe -m satellite --title Portrait" in banner

    def test_appends_so_a_prior_session_survives_the_next_launch(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        log_file.write_text("Traceback from the session that died\n", encoding="utf-8")

        with open_child_log(log_file, ["pythonw.exe"]):
            pass

        assert "Traceback from the session that died" in log_file.read_text(encoding="utf-8")

    def test_rolls_the_log_aside_once_it_passes_the_cap(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        log_file.write_text("x" * 200, encoding="utf-8")

        with open_child_log(log_file, ["pythonw.exe"], max_bytes=100):
            pass

        assert (tmp_path / "portrait_satellite.log.1").read_text(encoding="utf-8") == "x" * 200
        assert "x" * 200 not in log_file.read_text(encoding="utf-8")

    def test_creates_the_state_directory(self, tmp_path: Path):
        # A player can be launched before anything has created state/, and the
        # log has to exist before the child it is capturing starts.
        log_file = tmp_path / "state" / "portrait_satellite.log"

        with open_child_log(log_file, ["pythonw.exe"]):
            pass

        assert log_file.exists()
