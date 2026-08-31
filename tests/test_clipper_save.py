"""The clipper save itself — the cross-repo contract with clipper.create_session.

The dispatcher only raises the ``save_clip`` op (tests/test_command_dispatch.py);
the loop runs it on a worker thread (tests/test_windows_bridge_dispatch_loop.py).
What runs is pinned here: the exact command line, the toast on success, the
silence on failure.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fun_time.bridge_records import BridgeConfig
from fun_time.clipper_save import save_clip_session


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
        genau_mode_file=state_dir / "genau_mode.txt",
        genau_cmd_file=state_dir / "genau_cmd.txt",
        genau_paused_file=state_dir / "genau_paused.txt",
        audio_paused_file=state_dir / "audio_paused.txt",
        audio_volume_file=state_dir / "audio_volume.txt",
        nau_cmd_file=state_dir / "nau_cmd.txt",
        nau_paused_file=state_dir / "nau_paused.txt",
        nau_status_file=state_dir / "nau_status.txt",
        dashboard_state_file=state_dir / "dashboard_state.ini",
    )


def test_the_save_runs_clippers_venv_on_naus_video_and_position(tmp_path: Path):
    config = _make_config(tmp_path)
    config.nau_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\nstate=normal\npaused=0\n",
        encoding="utf-8",
    )

    with patch("fun_time.clipper_save._clipper_python", return_value="python"), \
         patch("fun_time.clipper_save.subprocess") as mock_subprocess:
        mock_subprocess.run.return_value.returncode = 0
        mock_subprocess.run.return_value.stdout = r"C:\clipper\sessions\test.json"
        mock_subprocess.run.return_value.stderr = ""
        message = save_clip_session(config)

    mock_subprocess.run.assert_called_once()
    cmd = mock_subprocess.run.call_args[0][0]
    assert cmd[0] == "python"
    assert "-m" in cmd
    assert "clipper.create_session" in cmd
    assert "--video" in cmd
    assert r"C:\videos\test.mp4" in cmd
    assert "--time" in cmd
    assert "42.5" in cmd
    # The toast names the session after the path's stem.  Windows path
    # splitting differs off Windows, so the pin is the shape, not the equality.
    assert message.startswith("Clipper: ")
    assert "test" in message


def test_a_failed_save_answers_empty_so_nothing_flashes(tmp_path: Path):
    config = _make_config(tmp_path)
    config.nau_status_file.write_text(
        "video=C:\\videos\\test.mp4\nposition_ms=42500\n", encoding="utf-8",
    )

    with patch("fun_time.clipper_save._clipper_python", return_value="python"), \
         patch("fun_time.clipper_save.subprocess") as mock_subprocess:
        mock_subprocess.run.return_value.returncode = 1
        mock_subprocess.run.return_value.stdout = ""
        mock_subprocess.run.return_value.stderr = "ffprobe failed"
        message = save_clip_session(config)

    assert message == ""


def test_no_video_playing_means_no_subprocess_at_all(tmp_path: Path):
    config = _make_config(tmp_path)
    # No nau_status file → no current video → nothing to clip.

    with patch("fun_time.clipper_save.subprocess") as mock_subprocess:
        message = save_clip_session(config)

    mock_subprocess.run.assert_not_called()
    assert message == ""
