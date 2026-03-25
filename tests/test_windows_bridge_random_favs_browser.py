from __future__ import annotations

import subprocess
from pathlib import Path

from fun_time.windows_bridge_random_favs_browser import (
    build_random_favs_browser_launch_plan,
    launch_random_favs_browser,
    read_random_favs_browser_manifest,
)


def test_read_random_favs_browser_manifest_returns_profile_and_urls(tmp_path: Path):
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text(
        "Profile 2\nhttps://example.com/1\nhttps://example.com/2\n",
        encoding="utf-8",
    )

    manifest = read_random_favs_browser_manifest(manifest_file)

    assert manifest.profile_dir == "Profile 2"
    assert manifest.urls == ["https://example.com/1", "https://example.com/2"]


def test_build_random_favs_browser_launch_plan_adds_profile_and_new_window(tmp_path: Path):
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text(
        "Profile 2\nhttps://example.com/1\n",
        encoding="utf-8",
    )

    plan = build_random_favs_browser_launch_plan(
        manifest_file,
        shortcut_target=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        shortcut_work_dir=r"C:\Program Files\Google\Chrome\Application",
        shortcut_args='--disable-features="Something"',
    )

    assert plan.should_launch is True
    assert '--profile-directory="Profile 2"' in plan.cmd
    assert "--new-window" in plan.cmd
    assert "https://example.com/1" in plan.cmd
    assert plan.work_dir == r"C:\Program Files\Google\Chrome\Application"


def test_build_random_favs_browser_launch_plan_preserves_existing_shortcut_args_text(tmp_path: Path):
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text(
        "Profile 2\nhttps://example.com/1\n",
        encoding="utf-8",
    )

    plan = build_random_favs_browser_launch_plan(
        manifest_file,
        shortcut_target=r"C:\Chrome\chrome.exe",
        shortcut_work_dir=r"C:\Chrome",
        shortcut_args='--disable-features="Something With Spaces"',
    )

    assert '--disable-features="Something With Spaces"' in plan.cmd
    assert '"https://example.com/1"' in plan.cmd


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
        shortcut_target=r"C:\Chrome\chrome.exe",
        shortcut_work_dir=r"C:\Chrome",
        shortcut_args='--profile-directory="Profile 2"',
    )

    assert plan.should_launch is True
    assert recorded["cmd"] == plan.cmd
    assert recorded["cwd"] == r"C:\Chrome"
