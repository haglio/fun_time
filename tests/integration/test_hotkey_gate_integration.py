"""The gate in front of the letter hotkeys, asked its question with no window up.

``#HotIf !OrigeneratorHasKeyboard()`` is evaluated by AutoHotkey's keyboard hook
before it decides whether a bare letter belongs to the session or to whatever is
focused, so an error raised inside it has no call site to catch it: AutoHotkey
puts its own error dialog on the screen instead, once per key pressed.

The unit suite can only read this function's text.  Handing it to a real
AutoHotkey on a desktop with no windows on it is what actually asks it the
question Windows answers with NULL.
"""
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


def _harness(tmp_path: Path) -> Path:
    """A script that calls the real gate once and writes down its answer.

    No settings are set: a ``#HotIf`` expression is evaluated with AutoHotkey's
    defaults rather than the script's, and this asks the question the same way.
    """
    script = tmp_path / "gate.ahk"
    script.write_text(
        "#Requires AutoHotkey v2.0\n"
        "#NoTrayIcon\n\n"
        f"{function_source('OrigeneratorHasKeyboard')}\n\n"
        'FileAppend(OrigeneratorHasKeyboard() ? "yes" : "no", A_Args[1], "UTF-8-RAW")\n'
        "ExitApp\n",
        encoding="utf-8",
    )
    return script


def test_the_gate_leaves_the_hotkeys_live_when_no_window_holds_the_focus(tmp_path: Path):
    """Nothing focused is not Origenerator focused, so the session keeps its keys.

    Windows names no foreground window while one is being destroyed, across a
    handover between two apps, or while the secure desktop is in front — all of
    which a session full of players opening and closing walks through routinely.
    """
    answer = tmp_path / "answer.txt"

    exit_code = run_where_nothing_is_focused(
        [str(load_config(real_config_path()).paths.ahk_exe), str(_harness(tmp_path)), str(answer)]
    )

    assert exit_code == 0, "the gate did not run to completion with no window focused"
    assert answer.read_text(encoding="utf-8") == "no"
