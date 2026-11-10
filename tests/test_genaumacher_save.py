from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.bridge_records import BridgeConfig
from fun_time.genaumacher_save import save_and_say, save_clip_session
from tests.bridge_configs import made_up_bridge_config


def _make_config(tmp_path: Path) -> BridgeConfig:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return made_up_bridge_config(
        state_dir,
        trash_dir=tmp_path / "trash",
        main_sources=str(tmp_path / "primary"),
    )


def test_the_save_runs_genaumachers_venv_on_main_funestras_video_and_position(tmp_path: Path):
    config = _make_config(tmp_path)
    config.main_funestra_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\nloop_state=normal\npaused=0\n",
        encoding="utf-8",
    )

    checkout = tmp_path / "genaumacher"
    with patch("fun_time.genaumacher_save._genaumacher_project_dir", return_value=checkout), \
         patch("fun_time.genaumacher_save._genaumacher_python", return_value="python"), \
         patch("fun_time.genaumacher_save.subprocess") as mock_subprocess:
        mock_subprocess.run.return_value.returncode = 0
        mock_subprocess.run.return_value.stdout = r"C:\genaumacher\sessions\test.json"
        mock_subprocess.run.return_value.stderr = ""
        message = save_clip_session(config)

    mock_subprocess.run.assert_called_once()
    cmd = mock_subprocess.run.call_args[0][0]
    assert cmd[0] == "python"
    assert "-m" in cmd
    assert "genaumacher.create_session" in cmd
    assert mock_subprocess.run.call_args.kwargs["cwd"] == str(checkout)
    assert "--video" in cmd
    assert r"C:\videos\test.mp4" in cmd
    assert "--time" in cmd
    assert "42.5" in cmd
    # The message names the session after the path's stem.  Windows path
    # splitting differs off Windows, so the pin is the shape, not the equality.
    assert message.startswith("Genaumacher: ")
    assert "test" in message


def test_a_failed_save_says_nothing_but_its_log_line(tmp_path: Path):
    config = _make_config(tmp_path)
    config.main_funestra_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\n", encoding="utf-8",
    )

    with patch("fun_time.genaumacher_save._genaumacher_python", return_value="python"), \
         patch("fun_time.genaumacher_save.subprocess") as mock_subprocess:
        mock_subprocess.run.return_value.returncode = 1
        mock_subprocess.run.return_value.stdout = ""
        mock_subprocess.run.return_value.stderr = "ffprobe failed"
        message = save_clip_session(config)

    assert message == ""


@pytest.mark.parametrize("failure", [
    {"return_value": subprocess.CompletedProcess(["genaumacher"], 1, stdout="", stderr="ffprobe failed")},
    {"side_effect": subprocess.TimeoutExpired(cmd="genaumacher", timeout=10)},
])
def test_a_failed_save_is_logged_as_an_error(tmp_path: Path, caplog, failure):
    config = _make_config(tmp_path)
    config.main_funestra_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\n", encoding="utf-8",
    )

    with patch("fun_time.genaumacher_save._genaumacher_python", return_value="python"), \
         patch("fun_time.genaumacher_save.subprocess.run", **failure), \
         caplog.at_level(logging.DEBUG, logger="fun_time.genaumacher_save"):
        save_clip_session(config)

    assert [r.levelno for r in caplog.records if r.name == "fun_time.genaumacher_save"] == [
        logging.ERROR]


def test_no_video_playing_means_no_subprocess_at_all(tmp_path: Path):
    config = _make_config(tmp_path)

    with patch("fun_time.genaumacher_save.subprocess") as mock_subprocess:
        message = save_clip_session(config)

    mock_subprocess.run.assert_not_called()
    assert message == ""


def test_a_timeout_reads_as_a_failed_save_not_a_crash(tmp_path: Path):
    config = _make_config(tmp_path)
    config.main_funestra_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\n", encoding="utf-8",
    )

    with patch("fun_time.genaumacher_save._genaumacher_python", return_value="python"), \
         patch("fun_time.genaumacher_save.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="genaumacher", timeout=10)):
        message = save_clip_session(config)

    assert message == ""


def test_a_bug_of_our_own_is_never_reported_as_genaumachers(tmp_path: Path):
    config = _make_config(tmp_path)
    config.main_funestra_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\n", encoding="utf-8",
    )

    with patch("fun_time.genaumacher_save._genaumacher_python", return_value="python"), \
         patch("fun_time.genaumacher_save.subprocess.run", side_effect=TypeError("bug")), \
         pytest.raises(TypeError):
        save_clip_session(config)


def test_a_failed_save_flashes_no_empty_box(tmp_path: Path, caplog):
    with patch("fun_time.genaumacher_save.save_clip_session", return_value=""), \
         caplog.at_level(logging.DEBUG, logger="fun_time.genaumacher_save"):
        save_and_say(_make_config(tmp_path))

    assert [record.message for record in caplog.records] == []
