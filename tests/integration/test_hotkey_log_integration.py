"""The hotkey script and the orchestrator both append to the bridge log.

A run polls that log for the line a session start waits on.  The script's
FileAppend took the file exclusively, and a line that met another handle was
retried three times and dropped; shared, the two writers' appends landed on each
other's offsets instead.  Either way a start that lost "Session up" timed out
with its session fully up.
"""
from __future__ import annotations

import logging
import re
import sys
import threading
from pathlib import Path

import pytest

from fun_time.config import load_config
from fun_time.windows_bridge_orchestrator import _AppendOnWriteHandler
from tests.ahk_script import function_source

from .hidden_desktop import run_where_nothing_is_focused
from .integration_support import real_config_path

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="AutoHotkey is the Windows side of the bridge",
)

LINE = "Session up; startup hold released"
SCRIPT_LINES = 2000


def _logging_script(tmp_path: Path, body: str) -> Path:
    """A script that has the real Log() and runs *body* with it."""
    script = tmp_path / "log.ahk"
    script.write_text(
        "#Requires AutoHotkey v2.0\n"
        "#NoTrayIcon\n\n"
        "WINDOWS_BRIDGE_LOG_FILE := A_Args[1]\n\n"
        f"{function_source('AppendWithRetry')}\n\n"
        f"{function_source('Log')}\n\n"
        f"{body}\n"
        "ExitApp\n",
        encoding="utf-8",
    )
    return script


def _ahk_exe() -> str:
    return str(load_config(real_config_path()).paths.ahk_exe)


@pytest.mark.parametrize("mode", ["r", "a"], ids=["while_polled", "while_appended_to"])
def test_a_logged_line_lands_while_the_log_is_open_elsewhere(tmp_path: Path, mode: str):
    log = tmp_path / "windows_bridge.log"
    log.touch()
    script = _logging_script(tmp_path, f'Log("{LINE}")')

    with log.open(mode, encoding="utf-8"):
        exit_code = run_where_nothing_is_focused([_ahk_exe(), str(script), str(log)])

    assert exit_code == 0, "the hotkey script did not run to completion"
    assert LINE in log.read_text(encoding="utf-8"), "the line was dropped"


def test_neither_writer_loses_a_line_to_the_other(tmp_path: Path):
    log = tmp_path / "windows_bridge.log"
    script = _logging_script(
        tmp_path, f'loop {SCRIPT_LINES} {{\n    Log("hotkey line " . A_Index)\n}}')
    orchestrator = logging.getLogger(f"{__name__}.orchestrator")
    orchestrator.propagate = False
    orchestrator.setLevel(logging.INFO)
    handler = _AppendOnWriteHandler(log)
    orchestrator.addHandler(handler)
    finished = threading.Event()
    sent = 0

    def append_until_the_script_is_done():
        nonlocal sent
        while not finished.is_set():
            sent += 1
            orchestrator.info("orchestrator line %d", sent)

    writer = threading.Thread(target=append_until_the_script_is_done)
    writer.start()
    try:
        exit_code = run_where_nothing_is_focused(
            [_ahk_exe(), str(script), str(log)], timeout_seconds=120)
    finally:
        finished.set()
        writer.join()
        orchestrator.removeHandler(handler)

    text = log.read_text(encoding="utf-8")
    script_landed = {int(n) for n in re.findall(r"hotkey line (\d+)\n", text)}
    orchestrator_landed = {int(n) for n in re.findall(r"orchestrator line (\d+)\n", text)}

    assert exit_code == 0, "the hotkey script did not run to completion"
    assert SCRIPT_LINES - len(script_landed) == 0, "the hotkey script lost lines to the orchestrator"
    assert sent - len(orchestrator_landed) == 0, "the orchestrator lost lines to the hotkey script"
