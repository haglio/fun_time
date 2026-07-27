"""The hotkey script runs headless — no tray icon, no tray menu."""
from __future__ import annotations

from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "windows_bridge_hotkeys.ahk"


def _script_text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def test_script_suppresses_the_tray_icon():
    """AutoHotkey gives a persistent script a tray icon unless told otherwise.

    The directive is the only thing between this process and an icon in the
    notification area, so dropping the line silently puts it back.
    """
    assert "#NoTrayIcon" in _script_text()


def test_script_builds_no_tray_menu():
    """Nothing dresses an icon that is never shown.

    A suppressed icon still accepts ``TraySetIcon``/``A_TrayMenu`` calls without
    complaint, so the menu could sit here indefinitely as code that runs and
    reaches no one.
    """
    text = _script_text()
    for call in ("TraySetIcon", "A_IconTip", "A_TrayMenu"):
        assert call not in text, f"{call} dresses a tray icon the script does not show"
