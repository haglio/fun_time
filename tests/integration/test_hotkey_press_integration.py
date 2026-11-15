from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
from pathlib import Path

import pytest
from app_support.win32_loader import load_dll

from fun_time.config import load_config
from tests.ahk_script import THE_RESIDENT_ENTRYPOINT_NO_TEST_CAN_IMPORT, hotkeys_in_order

from .integration_support import ended_within, seen_within, the_machines_config_path

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="AutoHotkey is the Windows side of the bridge",
)

WM_HOTKEY = 0x0312
SESSION_UP = "Session up; startup hold released"

_user32 = load_dll("user32", use_last_error=True)
_ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
_user32.EnumWindows.argtypes = [_ENUM_PROC, ctypes.wintypes.LPARAM]
_user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
_user32.GetClassNameW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPWSTR, ctypes.c_int]
_user32.PostMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT,
                                 ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
_user32.PostMessageW.restype = ctypes.wintypes.BOOL


def _the_scripts_window(pid: int) -> int | None:
    found: list[int] = []

    def look(hwnd, _lparam):
        owner = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        name = ctypes.create_unicode_buffer(64)
        _user32.GetClassNameW(hwnd, name, 64)
        if owner.value == pid and name.value == "AutoHotkey":
            found.append(hwnd)
        return True

    _user32.EnumWindows(_ENUM_PROC(look), 0)
    return found[0] if found else None


class _TheHotkeyScript:
    def __init__(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        state.mkdir()
        self.commands = tmp_path / "dashboard_cmd.txt"
        self.log = state / "windows_bridge.log"
        self.ahk_commands = state / "ahk_cmd.txt"
        manifest = tmp_path / "manifest.ini"
        manifest.write_text(
            f"[commands]\ndashboard_cmd_file={self.commands}\n\n"
            f"[runtime]\nwindows_bridge_log_file={self.log}\n", encoding="utf-8")
        pids = tmp_path / "pids.txt"
        pids.write_text("", encoding="utf-8")
        ahk_exe = str(load_config(the_machines_config_path()).paths.ahk_exe)
        self.process = subprocess.Popen(
            [ahk_exe, "/ErrorStdOut", str(THE_RESIDENT_ENTRYPOINT_NO_TEST_CAN_IMPORT),
             str(manifest), str(pids), str(os.getpid())])
        self.window: int | None = None

    def wait_until_live(self) -> None:
        assert seen_within(lambda: self.log.exists() and SESSION_UP in self.log.read_text(
            encoding="utf-8", errors="replace"), seconds=30), "the hotkey script never went live"
        self.window = seen_within(lambda: _the_scripts_window(self.process.pid), seconds=10)
        assert self.window, "the hotkey script's window never appeared"

    def press(self, key: str) -> None:
        hotkey_id = list(hotkeys_in_order()).index(key)
        assert _user32.PostMessageW(self.window, WM_HOTKEY, hotkey_id, 0), ctypes.get_last_error()

    def queued(self) -> list[str]:
        try:
            return self.commands.read_text(encoding="utf-8").split()
        except FileNotFoundError:
            return []
        except PermissionError:
            return ["the script is still writing"]

    def tell(self, action: str) -> None:
        with self.ahk_commands.open("a", encoding="utf-8") as mailbox:
            mailbox.write(action + "\n")

    def close(self) -> None:
        self.tell("exit")
        if not ended_within(self.process, 10):
            self.process.kill()
            assert ended_within(self.process, 10), "the hotkey script outlived its kill"


@pytest.fixture
def hotkey_script(tmp_path):
    script = _TheHotkeyScript(tmp_path)
    try:
        script.wait_until_live()
        yield script
    finally:
        script.close()


def test_a_pressed_key_queues_the_command_its_line_names(hotkey_script):
    hotkey_script.press("b")
    hotkey_script.press("^Left")

    assert seen_within(
        lambda: hotkey_script.queued() == ["broker_panel", "portrait_hud_left"], seconds=10), (
        hotkey_script.queued())


def test_a_key_pressed_while_the_session_is_paused_queues_nothing(hotkey_script):
    hotkey_script.tell("suspend_hotkeys")
    assert seen_within(lambda: not hotkey_script.ahk_commands.exists(), seconds=10)

    hotkey_script.press("b")
    hotkey_script.tell("unsuspend_hotkeys")
    assert seen_within(lambda: not hotkey_script.ahk_commands.exists(), seconds=10)
    hotkey_script.press("d")

    assert seen_within(lambda: hotkey_script.queued() == ["landscape_next"], seconds=10), (
        hotkey_script.queued())
