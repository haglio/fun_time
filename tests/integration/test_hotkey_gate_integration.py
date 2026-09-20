"""The refresher for the letter hotkeys' gate, asked its question with no window up.

The gate in front of those hotkeys is a flag rather than a question, because
``#HotIf`` is evaluated by the script's main thread while the keyboard hook
holds the key.  ``WatchWhoHasTheKeyboard`` is what asks Windows instead, on a
timer, and it asks with ``GetForegroundWindow`` — which hands back nothing while
a window is being destroyed, across a handover between two apps, or with the
secure desktop in front.  A session full of players opening and closing walks
through all three routinely, and nothing focused has to read as "the session
keeps its keys".

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
    """A script that runs the real refresher once and writes down its answer.

    No settings are set: the refresher reaches for Windows itself rather than
    for AutoHotkey's window functions, so none of the script's title-matching
    settings bear on the answer, and this asks it the same way the timer does.
    """
    script = tmp_path / "gate.ahk"
    script.write_text(
        "#Requires AutoHotkey v2.0\n"
        "#NoTrayIcon\n\n"
        "global OrigeneratorHasKeyboard := false\n\n"
        f"{function_source('WatchWhoHasTheKeyboard')}\n\n"
        "WatchWhoHasTheKeyboard()\n"
        'FileAppend(OrigeneratorHasKeyboard ? "yes" : "no", A_Args[1], "UTF-8-RAW")\n'
        "ExitApp\n",
        encoding="utf-8",
    )
    return script


def test_the_gate_leaves_the_hotkeys_live_when_no_window_holds_the_focus(tmp_path: Path):
    """Nothing focused is not Origenerator focused, so the session keeps its keys.

    Reading the caption of the window Windows names is the whole of the answer,
    and there is no window to name here — so this is also what proves the
    refresher survives being handed nothing rather than reading past it.
    """
    answer = tmp_path / "answer.txt"

    exit_code = run_where_nothing_is_focused(
        [str(load_config(real_config_path()).paths.ahk_exe), str(_harness(tmp_path)), str(answer)]
    )

    assert exit_code == 0, "the refresher did not run to completion with no window focused"
    assert answer.read_text(encoding="utf-8") == "no"
