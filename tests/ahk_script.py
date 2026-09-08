"""Reading the hotkey script's source, for the tests that cannot call it.

``windows_bridge_hotkeys.ahk`` is an AutoHotkey entrypoint: it demands two
arguments, binds the session's hotkeys and then stays resident, so nothing can
import it and no test can reach one of its functions as it stands.  Lifting a
function out of its text is what lets a test either assert on it or hand it to a
real AutoHotkey to run.
"""
from __future__ import annotations

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "windows_bridge_hotkeys.ahk"


def script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def function_source(name: str) -> str:
    """One AHK function's whole definition, braces included.

    Reading a whole function rather than grepping the file keeps an assertion
    about one function from passing on a line that happens to sit in another.
    """
    text = script_text()
    for line in text.splitlines():
        if line.startswith(f"{name}(") and line.endswith("{"):
            start = text.index(f"\n{line}\n")
            return text[start:text.index("\n}\n", start) + len("\n}\n")].strip()
    raise AssertionError(f"the script defines no function named {name}")
