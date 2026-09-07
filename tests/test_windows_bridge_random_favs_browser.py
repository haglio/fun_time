from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

import pytest

from fun_time.random_favs_browser import write_manifest
from fun_time.rfb_tab_page import TabTarget, write_tab_pages
from fun_time.windows_bridge_random_favs_browser import (
    ChromeShortcut,
    build_open_rfb_tab_command,
    build_random_favs_browser_launch_plan,
    launch_random_favs_browser,
    open_rfb_tab,
    read_random_favs_browser_manifest,
)


def windows_argv(command_line: str) -> list[str]:
    """What Windows itself parses *command_line* into.

    The only judge that matters: this string is handed to CreateProcessW, which
    parses it with these rules and no others.  Asked of the OS rather than
    reimplemented, because a hand-rolled parser would agree with whatever we
    believed when we wrote it.
    """
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    count = ctypes.c_int(0)
    argv = shell32.CommandLineToArgvW(command_line, ctypes.byref(count))
    if not argv:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [argv[i] for i in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(argv)


needs_windows = pytest.mark.skipif(
    sys.platform != "win32", reason="CommandLineToArgvW is the Windows parser")


# --- Open RFB tab ---


@needs_windows
def test_build_open_rfb_tab_command_constructs_chrome_command():
    cmd = build_open_rfb_tab_command(
        urls=["https://example.com"],
        shortcut=ChromeShortcut(
            target=r"C:\Chrome\chrome.exe",
            work_dir="",
            args='--profile-directory="Profile 2"'),
    )

    assert windows_argv(cmd) == [
        r"C:\Chrome\chrome.exe", "--profile-directory=Profile 2", "https://example.com"]


@needs_windows
def test_build_open_rfb_tab_command_with_empty_args():
    cmd = build_open_rfb_tab_command(
        urls=["https://example.com"],
        shortcut=ChromeShortcut(
            target=r"C:\Chrome\chrome.exe",
            work_dir="",
            args=""),
    )

    assert windows_argv(cmd) == [r"C:\Chrome\chrome.exe", "https://example.com"]


@needs_windows
def test_build_open_rfb_tab_command_opens_multiple_urls_in_one_launch():
    """Both URLs go to a single chrome.exe invocation — launching chrome twice
    in quick succession races its singleton and drops a tab (the "lock both" bug)."""
    cmd = build_open_rfb_tab_command(
        urls=["https://example.com/1", "https://example.com/2"],
        shortcut=ChromeShortcut(
            target=r"C:\Chrome\chrome.exe",
            work_dir="",
            args=""),
    )

    assert windows_argv(cmd) == [
        r"C:\Chrome\chrome.exe", "https://example.com/1", "https://example.com/2"]


def test_open_rfb_tab_calls_subprocess(monkeypatch):
    recorded: dict[str, str] = {}

    def fake_popen(cmd, cwd):
        recorded["cmd"] = cmd
        recorded["cwd"] = cwd
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    open_rfb_tab(
        urls=["https://example.com/1", "https://example.com/2"],
        shortcut=ChromeShortcut(
            target=r"C:\Chrome\chrome.exe",
            work_dir=r"C:\Chrome",
            args='--profile-directory="Profile 2"'),
    )

    assert "chrome.exe" in recorded["cmd"]
    assert "https://example.com/1" in recorded["cmd"]
    assert "https://example.com/2" in recorded["cmd"]
    assert recorded["cwd"] == r"C:\Chrome"


# --- Manifest tests ---


def test_read_random_favs_browser_manifest_returns_profile_and_urls(tmp_path: Path):
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text(
        "Profile 2\nhttps://example.com/1\nhttps://example.com/2\n",
        encoding="utf-8",
    )

    manifest = read_random_favs_browser_manifest(manifest_file)

    assert manifest.profile_dir == "Profile 2"
    assert manifest.urls == ["https://example.com/1", "https://example.com/2"]


@needs_windows
def test_build_random_favs_browser_launch_plan_adds_profile_and_new_window(tmp_path: Path):
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text(
        "Profile 2\nhttps://example.com/1\n",
        encoding="utf-8",
    )

    plan = build_random_favs_browser_launch_plan(
        manifest_file,
        shortcut=ChromeShortcut(
            target=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            work_dir=r"C:\Program Files\Google\Chrome\Application",
            args='--disable-features="Something"'),
    )

    assert plan.should_launch is True
    assert windows_argv(plan.cmd) == [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "--disable-features=Something",
        "--profile-directory=Profile 2",
        "--new-window",
        "https://example.com/1",
    ]
    assert plan.work_dir == r"C:\Program Files\Google\Chrome\Application"


@needs_windows
def test_build_random_favs_browser_launch_plan_preserves_existing_shortcut_args_text(tmp_path: Path):
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text(
        "Profile 2\nhttps://example.com/1\n",
        encoding="utf-8",
    )

    plan = build_random_favs_browser_launch_plan(
        manifest_file,
        shortcut=ChromeShortcut(
            target=r"C:\Chrome\chrome.exe",
            work_dir=r"C:\Chrome",
            args='--disable-features="Something With Spaces"'),
    )

    # The shortcut's own text is spliced in verbatim: the .lnk already stores it
    # as command-line text, so re-quoting it would change what Chrome is told.
    assert '--disable-features="Something With Spaces"' in plan.cmd
    assert windows_argv(plan.cmd)[1] == "--disable-features=Something With Spaces"
    assert windows_argv(plan.cmd)[-1] == "https://example.com/1"


def test_launch_random_favs_browser_uses_subprocess(tmp_path: Path, monkeypatch):
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text(
        "Profile 2\nhttps://example.com/1\n",
        encoding="utf-8",
    )

    recorded: dict[str, str] = {}

    def fake_popen(cmd, cwd):
        recorded["cmd"] = cmd
        recorded["cwd"] = cwd
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    plan = launch_random_favs_browser(
        manifest_file,
        shortcut=ChromeShortcut(
            target=r"C:\Chrome\chrome.exe",
            work_dir=r"C:\Chrome",
            args='--profile-directory="Profile 2"'),
    )

    assert plan.should_launch is True
    assert recorded["cmd"] == plan.cmd
    assert recorded["cwd"] == r"C:\Chrome"


# --- Command-line ceiling ---

# CreateProcess rejects a command line of 32,767 characters or more.
_WINDOWS_COMMAND_LINE_LIMIT = 32767


def test_ten_regenerate_tabs_stay_under_the_windows_command_line_limit(tmp_path: Path):
    """A regenerate URL's #ft= payload must never reach Chrome's argv.

    Ten of them are ~50 KB — past the CreateProcess ceiling, which fails the
    launch with WinError 206.  The payload rides in the lazy-load pages instead.
    """
    payload = "%22" * 1300  # a ~3.9 KB fragment, the size of a real prompt set
    targets = [
        TabTarget(url=f"https://example.com/create#ft={payload}", label=f"https://example.com/image/{i}")
        for i in range(10)
    ]
    manifest_file = tmp_path / "browser_manifest.txt"
    write_manifest(manifest_file, "Profile 2", write_tab_pages(tmp_path / "rfb_tabs", targets))

    plan = build_random_favs_browser_launch_plan(
        manifest_file,
        shortcut=ChromeShortcut(
            target=r"C:\Chrome\chrome.exe",
            work_dir=r"C:\Chrome",
            args=""),
    )

    assert plan.should_launch is True
    assert payload not in plan.cmd
    assert len(plan.cmd) < _WINDOWS_COMMAND_LINE_LIMIT


@needs_windows
def test_a_quote_in_a_profile_name_no_longer_malforms_the_command_line():
    """Values were wrapped in bare double quotes with no escaping, so one that
    contained a quote of its own closed the wrapper early and handed Chrome a
    different argument list than the one asked for."""
    cmd = build_open_rfb_tab_command(
        urls=[r'https://example.com/a"b'],
        shortcut=ChromeShortcut(target=r"C:\Chrome\chrome.exe", work_dir="", args=""),
    )

    assert windows_argv(cmd) == [r"C:\Chrome\chrome.exe", r'https://example.com/a"b']
