from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fun_time.runtime_support import (
    consume_command_file,
    hidden_subprocess_kwargs,
    open_child_log,
    preparse_config_path,
)


class TestPreparseConfigPath:
    def test_returns_none_without_config_arg(self):
        assert preparse_config_path([]) is None

    def test_extracts_config_arg_without_consuming_others(self):
        assert preparse_config_path(["--foo", "bar", "--config", "demo.json"]) == "demo.json"


class TestConsumeCommandFile:
    def test_returns_none_when_file_missing(self, tmp_path: Path):
        assert consume_command_file(tmp_path / "missing.txt") is None

    def test_returns_uppercased_text_and_clears_file(self, tmp_path: Path):
        command_file = tmp_path / "cmd.txt"
        command_file.write_text("  resume  ", encoding="utf-8")

        assert consume_command_file(command_file) == "RESUME"
        assert command_file.read_text(encoding="utf-8") == ""

    def test_logs_and_returns_none_on_read_failure(self, tmp_path: Path):
        logger = MagicMock()
        command_file = tmp_path / "cmd.txt"

        with patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", side_effect=OSError("boom")):
            assert consume_command_file(command_file, logger=logger) is None

        logger.exception.assert_called_once()


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


class TestHiddenSubprocessKwargs:
    def test_returns_empty_dict_on_non_windows(self):
        with patch("fun_time.runtime_support.os.name", "posix"), \
             patch("fun_time.runtime_support.sys.platform", "linux"):
            assert hidden_subprocess_kwargs() == {}

    def test_returns_windows_flags_on_nt(self):
        fake_startupinfo = MagicMock(dwFlags=0)
        with patch("fun_time.runtime_support.os.name", "nt"), \
             patch("fun_time.runtime_support.sys.platform", "win32"), \
             patch("fun_time.runtime_support.subprocess.STARTUPINFO", return_value=fake_startupinfo), \
             patch("fun_time.runtime_support.subprocess.STARTF_USESHOWWINDOW", 1), \
             patch("fun_time.runtime_support.subprocess.CREATE_NO_WINDOW", 2), \
             patch("fun_time.runtime_support.subprocess.SW_HIDE", 0):
            result = hidden_subprocess_kwargs()

        assert result["creationflags"] == 2
        assert result["startupinfo"] is fake_startupinfo
        assert fake_startupinfo.dwFlags == 1
