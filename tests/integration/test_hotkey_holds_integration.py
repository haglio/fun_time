from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fun_time.config import load_config
from tests.ahk_script import function_source

from .hidden_desktop import run_where_nothing_is_focused
from .integration_support import real_config_path

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="AutoHotkey is the Windows side of the bridge",
)


def _holds_script(tmp_path: Path, steps: tuple[str, ...]) -> Path:
    takes = "\n".join(f'Take("{step}")' for step in steps)
    script = tmp_path / "holds.ahk"
    script.write_text(
        "#Requires AutoHotkey v2.0\n"
        "#NoTrayIcon\n\n"
        "global AHK_CMD_FILE := A_Args[1]\n"
        "global REPORT := A_Args[2]\n"
        "global StartupSuspended := false\n"
        "global PauseHold := false\n"
        "global HeadsetOff := false\n"
        "global StartupPhase := false\n"
        "global EndingPhase := false\n\n"
        "#SuspendExempt true\n"
        "^!q::return\n"
        "Esc::return\n"
        "+Esc::return\n"
        "#SuspendExempt false\n"
        "k::return\n\n"
        "KeepOrMarkSessionEnd(reason) {\n}\n\n"
        "EndTheSession() {\n}\n\n"
        f"{function_source('ProcessAhkCommand')}\n\n"
        f"{function_source('ApplyHolds')}\n\n"
        "Take(lines) {\n"
        "    FileAppend(StrReplace(lines, \"|\", \"`n\") . \"`n\", AHK_CMD_FILE, \"UTF-8\")\n"
        "    ProcessAhkCommand()\n"
        "    FileAppend(A_IsSuspended . \"`n\", REPORT)\n"
        "}\n\n"
        f"{takes}\n"
        "ExitApp\n",
        encoding="utf-8",
    )
    return script


def _suspended_after_each(tmp_path: Path, *steps: str) -> list[str]:
    mailbox = tmp_path / "ahk_cmd.txt"
    report = tmp_path / "suspended.txt"
    ahk_exe = str(load_config(real_config_path()).paths.ahk_exe)

    exit_code = run_where_nothing_is_focused(
        [ahk_exe, "/ErrorStdOut", str(_holds_script(tmp_path, steps)), str(mailbox), str(report)])

    assert exit_code == 0
    return report.read_text(encoding="utf-8").split()


def test_the_headset_coming_off_suspends_the_keys_and_going_back_on_lets_them_go(tmp_path):
    assert _suspended_after_each(tmp_path, "headset_off", "headset_on") == ["1", "0"]


def test_a_pause_ending_with_the_headset_off_leaves_the_keys_held(tmp_path):
    assert _suspended_after_each(
        tmp_path, "headset_off", "suspend_hotkeys", "unsuspend_hotkeys") == ["1", "1", "1"]


def test_the_headset_going_back_on_mid_pause_leaves_the_pause_holding(tmp_path):
    assert _suspended_after_each(
        tmp_path, "suspend_hotkeys", "headset_off", "headset_on") == ["1", "1", "1"]


def test_every_line_the_mailbox_holds_is_taken(tmp_path):
    assert _suspended_after_each(
        tmp_path, "headset_off|suspend_hotkeys", "unsuspend_hotkeys", "headset_on") == ["1", "1", "0"]
