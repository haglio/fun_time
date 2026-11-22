from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

from app_support import copyable_alert

from fun_time.project_paths import HOTKEY_SCRIPT_NAME, PROJECT_DIR
from fun_time.windows_bridge_orchestrator import COPYABLE_ALERT_COMMAND, launch_hotkey_script

SCRIPT = (PROJECT_DIR / HOTKEY_SCRIPT_NAME).read_text(encoding="utf-8")


def test_the_hotkey_script_is_handed_the_family_window_that_offers_its_message_to_copy(tmp_path: Path):
    with patch("fun_time.windows_bridge_orchestrator.subprocess.Popen") as popen:
        launch_hotkey_script(ahk_exe="AutoHotkey64.exe", script="hotkeys.ahk",
                             manifest_path=tmp_path / "manifest.ini",
                             pids_file=tmp_path / "pids.ini", project_dir=tmp_path)

    handed = popen.call_args.kwargs["env"]
    assert handed[COPYABLE_ALERT_COMMAND] == subprocess.list2cmdline(copyable_alert.COMMAND)


def test_the_hotkey_script_tells_that_window_what_to_say_under_the_family_names():
    for name in (COPYABLE_ALERT_COMMAND, copyable_alert.TITLE, copyable_alert.MESSAGE, copyable_alert.ICON):
        assert f'"{name}"' in SCRIPT


def test_every_error_the_hotkey_script_shows_goes_through_the_window_that_offers_copy():
    outside_tell = [line for line in SCRIPT.splitlines()
                    if re.search(r"\bMsgBox\(", line) and "Tell" not in line]

    assert len(outside_tell) == 1, outside_tell
