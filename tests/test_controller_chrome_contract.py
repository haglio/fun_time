from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_AHK = PROJECT_ROOT / "controller.ahk"


def _controller_text() -> str:
    return CONTROLLER_AHK.read_text(encoding="utf-8")


def test_chrome_overlay_targets_the_chrome_window_for_keystrokes():
    text = _controller_text()

    assert 'SendEvent(keys)' in text

    open_urls_start = text.index("OpenUrlsInChromeWindow(hwnd, urls) {")
    handle_in_list_start = text.index("HandleInList(hwnd, handles) {", open_urls_start)
    open_urls_block = text[open_urls_start:handle_in_list_start]

    assert 'SendChromeKeys(hwnd, "^t"' in open_urls_block
    assert 'SendChromeKeys(hwnd, "^l"' in open_urls_block
    assert 'SendChromeKeys(hwnd, "^v{Enter}"' in open_urls_block
    assert 'SendEvent("^t")' not in open_urls_block
    assert 'SendEvent("^l")' not in open_urls_block
    assert 'SendEvent("^v{Enter}")' not in open_urls_block


def test_chrome_overlay_refocuses_before_each_send():
    text = _controller_text()

    send_keys_start = text.index("SendChromeKeys(hwnd, keys, waitMs := 0) {")
    open_urls_start = text.index("OpenUrlsInChromeWindow(hwnd, urls) {", send_keys_start)
    send_keys_block = text[send_keys_start:open_urls_start]

    assert 'if (!FocusChromeWindow(hwnd))' in send_keys_block
    assert 'try ControlSend' not in send_keys_block
