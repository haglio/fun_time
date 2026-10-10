"""The genaumacher save itself — the cross-repo contract with genaumacher.create_session.

The dispatcher only raises the ``save_clip`` op (tests/test_command_dispatch.py);
the loop runs it on a worker thread (tests/test_windows_bridge_dispatch_loop.py).
What runs is pinned here: the exact command line, the notice on success, and on
failure no notice of its own, only the error it logs.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.bridge_records import BridgeConfig
from fun_time.genaumacher_save import save_clip_session


def _make_config(tmp_path: Path) -> BridgeConfig:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return BridgeConfig(
        portrait_cmd_file=state_dir / "portrait_cmd.txt",
        portrait_paused_file=state_dir / "portrait_paused.txt",
        portrait_status_file=state_dir / "portrait_status.txt",
        portrait_playlist_file=state_dir / "portrait_playlist.tsv",
        landscape_cmd_file=state_dir / "landscape_cmd.txt",
        landscape_paused_file=state_dir / "landscape_paused.txt",
        landscape_status_file=state_dir / "landscape_status.txt",
        landscape_playlist_file=state_dir / "landscape_playlist.tsv",
        favs_file=tmp_path / "favs.csv",
        weird_dir=tmp_path / "weird",
        state_dir=state_dir,
        main_sources=str(tmp_path / "primary"),
        portrait_sources=str(tmp_path / "portrait"),
        landscape_sources=str(tmp_path / "landscape"),
        broker_mode_file=state_dir / "broker_mode.txt",
        genau_cmd_file=state_dir / "genau_cmd.txt",
        genau_paused_file=state_dir / "genau_paused.txt",
        genau_status_file=state_dir / "genau_status.txt",
        audio_paused_file=state_dir / "audio_paused.txt",
        audio_volume_file=state_dir / "audio_volume.txt",
        main_player_cmd_file=state_dir / "main_player_cmd.txt",
        main_player_paused_file=state_dir / "main_player_paused.txt",
        main_player_status_file=state_dir / "main_player_status.txt",
        dashboard_state_file=state_dir / "dashboard_state.ini",
    )


def test_the_save_runs_genaumachers_venv_on_main_players_video_and_position(tmp_path: Path):
    config = _make_config(tmp_path)
    config.main_player_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\nloop_state=normal\npaused=0\n",
        encoding="utf-8",
    )

    checkout = _a_checkout_holding(tmp_path / "checkout", "genaumacher")
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
    assert "--video" in cmd
    assert r"C:\videos\test.mp4" in cmd
    assert "--time" in cmd
    assert "42.5" in cmd
    # The notice names the session after the path's stem.  Windows path
    # splitting differs off Windows, so the pin is the shape, not the equality.
    assert message.startswith("Genaumacher: ")
    assert "test" in message


def test_a_failed_save_answers_empty(tmp_path: Path):
    config = _make_config(tmp_path)
    config.main_player_status_file.write_text(
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
    """The save that was asked for did not happen, and the log line is the only
    word of it that flashes, so it reads red rather than a warning's yellow."""
    config = _make_config(tmp_path)
    config.main_player_status_file.write_text(
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
    # No main_player_status file → no current video → nothing to clip.

    with patch("fun_time.genaumacher_save.subprocess") as mock_subprocess:
        message = save_clip_session(config)

    mock_subprocess.run.assert_not_called()
    assert message == ""


def test_a_timeout_reads_as_a_failed_save_not_a_crash(tmp_path: Path):
    config = _make_config(tmp_path)
    config.main_player_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\n", encoding="utf-8",
    )

    with patch("fun_time.genaumacher_save._genaumacher_python", return_value="python"), \
         patch("fun_time.genaumacher_save.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd="genaumacher", timeout=10)):
        message = save_clip_session(config)

    assert message == ""


def test_a_bug_in_our_own_argument_building_surfaces(tmp_path: Path):
    """The old bare `except Exception` read a TypeError in our own code as
    "genaumacher failed"; only the OS and subprocess failures are genaumacher's."""
    config = _make_config(tmp_path)
    config.main_player_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\n", encoding="utf-8",
    )

    with patch("fun_time.genaumacher_save._genaumacher_python", return_value="python"), \
         patch("fun_time.genaumacher_save.subprocess.run", side_effect=TypeError("bug")), \
         pytest.raises(TypeError):
        save_clip_session(config)


def _a_checkout_holding(root: Path, package: str) -> Path:
    (root / package).mkdir(parents=True)
    (root / package / "create_session.py").touch()
    return root


@pytest.mark.parametrize("package", ["genaumacher", "clipper"])
def test_the_save_runs_the_package_the_checkout_holds(tmp_path: Path, package):
    checkout = _a_checkout_holding(tmp_path / "checkout", package)
    config = _make_config(tmp_path)
    config.main_player_status_file.write_text(
        "video=C:\videos\test.mp4\nposition_ms=42500\n", encoding="utf-8",
    )

    with patch("fun_time.genaumacher_save._genaumacher_project_dir", return_value=checkout), \
         patch("fun_time.genaumacher_save._genaumacher_python", return_value="python"), \
         patch("fun_time.genaumacher_save.subprocess") as mock_subprocess:
        mock_subprocess.run.return_value.returncode = 0
        mock_subprocess.run.return_value.stdout = r"C:\checkout\sessions\test.json"
        save_clip_session(config)

    assert f"{package}.create_session" in mock_subprocess.run.call_args[0][0]
