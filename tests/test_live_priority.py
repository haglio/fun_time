from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time import orchestrator
from fun_time.live_priority import put_this_session_ahead_of_background_work
from fun_time.windows_bridge_orchestrator import launch_hotkey_script
from fun_time_vr import orchestrator as vr_orchestrator


def test_the_session_puts_itself_ahead_of_background_work():
    asked = []

    put_this_session_ahead_of_background_work(put=lambda wanted: asked.append(wanted) or 0x20)

    assert asked == [subprocess.ABOVE_NORMAL_PRIORITY_CLASS]


def test_a_refused_raise_is_said_and_the_session_goes_on(caplog):
    put_this_session_ahead_of_background_work(put=lambda _wanted: None)

    assert "ahead of background work" in caplog.text


@pytest.mark.parametrize("program", [orchestrator, vr_orchestrator], ids=["desktop", "headset"])
def test_each_orchestrator_raises_itself_before_anything_else(program, tmp_path: Path):
    with patch.object(program, "put_this_session_ahead_of_background_work") as raised, \
            pytest.raises(FileNotFoundError):
        program.main(["--config", str(tmp_path / "no config here.json")])

    raised.assert_called_once_with()


def test_the_hotkey_script_starts_ahead_of_background_work_with_no_window(tmp_path: Path):
    with patch("fun_time.windows_bridge_orchestrator.subprocess.Popen") as popen:
        launch_hotkey_script(ahk_exe="AutoHotkey64.exe", script="hotkeys.ahk",
                             manifest_path=tmp_path / "manifest.ini",
                             pids_file=tmp_path / "pids.ini", project_dir=tmp_path)

    assert popen.call_args.kwargs["creationflags"] == (
        subprocess.CREATE_NO_WINDOW | subprocess.ABOVE_NORMAL_PRIORITY_CLASS)
