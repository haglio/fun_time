from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_AHK = PROJECT_ROOT / "controller.ahk"


def _controller_text() -> str:
    return CONTROLLER_AHK.read_text(encoding="utf-8")


def test_chrome_overlay_targets_the_chrome_window_for_keystrokes():
    text = _controller_text()

    assert "OpenUrlsInChromeWindow" not in text
    assert "SendChromeKeys" not in text


def test_chrome_overlay_refocuses_before_each_send():
    text = _controller_text()

    assert "FocusChromeWindow" not in text
    assert "ControlSend" in text


def test_chrome_overlay_launches_urls_via_chrome_command_line():
    text = _controller_text()

    maybe_launch_start = text.index("MaybeLaunchChromeOverlay(pidM) {")
    read_manifest_start = text.index("ReadChromeOverlayManifest(path) {", maybe_launch_start)
    maybe_launch_block = text[maybe_launch_start:read_manifest_start]

    assert "BuildChromeLaunchSpec(manifest)" in maybe_launch_block
    assert "OpenUrlsInChromeWindow" not in maybe_launch_block
    assert "--new-window" in text
    assert "FileGetShortcut" in text
