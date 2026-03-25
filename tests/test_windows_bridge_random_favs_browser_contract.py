from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_SHIM_AHK = PROJECT_ROOT / "controller.ahk"
WINDOWS_BRIDGE_AHK = PROJECT_ROOT / "windows_bridge.ahk"
WINDOWS_BRIDGE_WINDOWS_AHK = PROJECT_ROOT / "windows_bridge_windows.ahk"
WINDOWS_BRIDGE_RUNTIME_AHK = PROJECT_ROOT / "windows_bridge_runtime.ahk"
WINDOWS_BRIDGE_ACTIONS_AHK = PROJECT_ROOT / "windows_bridge_actions.ahk"


def _windows_bridge_text() -> str:
    return (
        WINDOWS_BRIDGE_AHK.read_text(encoding="utf-8")
        + "\n"
        + WINDOWS_BRIDGE_WINDOWS_AHK.read_text(encoding="utf-8")
        + "\n"
        + WINDOWS_BRIDGE_RUNTIME_AHK.read_text(encoding="utf-8")
        + "\n"
        + WINDOWS_BRIDGE_ACTIONS_AHK.read_text(encoding="utf-8")
    )


def test_random_favs_browser_targets_the_browser_window_for_keystrokes():
    text = _windows_bridge_text()

    assert "OpenUrlsInChromeWindow" not in text
    assert "SendChromeKeys" not in text


def test_random_favs_browser_refocuses_before_each_send():
    text = _windows_bridge_text()

    assert "FocusChromeWindow" not in text
    assert "ControlSend" in text


def test_random_favs_browser_launches_urls_via_browser_command_line():
    text = _windows_bridge_text()

    maybe_launch_start = text.index("MaybeLaunchRandomFavsBrowser(pidM) {")
    visible_handles_start = text.index("GetVisibleChromeWindowSnapshot() {", maybe_launch_start)
    maybe_launch_block = text[maybe_launch_start:visible_handles_start]

    assert "LaunchRandomFavsBrowserViaPython(" in maybe_launch_block
    assert "WaitForChromeLaunchWindow(existing, 8000)" in maybe_launch_block
    assert "BuildRandomFavsBrowserLaunchSpec(" not in text
    assert "ReadRandomFavsBrowserManifest(" not in text
    assert "FileGetShortcut" in text


def test_random_favs_browser_detects_retargeted_existing_chrome_window():
    text = _windows_bridge_text()

    assert "GetVisibleChromeWindowSnapshot() {" in text
    assert "WaitForChromeLaunchWindow(existingWindows, timeoutMs := 8000) {" in text
    assert 'previousTitle := GetChromeWindowTitle(window.hwnd, existingWindows)' in text
    assert 'if (previousTitle != "" && previousTitle != window.title)' in text
    assert "HandleInChromeWindowSnapshot(hwnd, windows) {" in text

