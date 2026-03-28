from __future__ import annotations

import subprocess
from pathlib import Path

from fun_time.windows_bridge_random_favs_browser import (
    build_random_favs_browser_launch_plan,
    launch_random_favs_browser,
    read_random_favs_browser_manifest,
    tab_placeholder_path,
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


# --- Lazy-load placeholder tests ---


def test_tab_placeholder_path_returns_existing_html_file():
    path = tab_placeholder_path()
    assert path.exists()
    assert path.suffix == ".html"
    content = path.read_text(encoding="utf-8")
    assert "reload" in content.lower()


def test_build_launch_plan_with_placeholder_wraps_urls(tmp_path: Path):
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text(
        "Profile 2\nhttps://example.com/1\nhttps://example.com/2\n",
        encoding="utf-8",
    )
    placeholder = tmp_path / "placeholder.html"
    placeholder.write_text("<html></html>", encoding="utf-8")

    plan = build_random_favs_browser_launch_plan(
        manifest_file,
        shortcut_target=r"C:\Chrome\chrome.exe",
        shortcut_work_dir=r"C:\Chrome",
        shortcut_args="",
        placeholder_path=placeholder,
    )

    assert plan.should_launch is True
    # Raw URLs must NOT appear as direct arguments
    assert '"https://example.com/1"' not in plan.cmd
    assert '"https://example.com/2"' not in plan.cmd
    # Instead, file:// URIs pointing to the placeholder must appear
    assert "file:///" in plan.cmd
    assert "placeholder.html" in plan.cmd
    assert "url=https%3A%2F%2Fexample.com%2F1" in plan.cmd
    assert "url=https%3A%2F%2Fexample.com%2F2" in plan.cmd


def test_build_launch_plan_without_placeholder_uses_raw_urls(tmp_path: Path):
    """Backward compat: no placeholder_path means direct URL loading."""
    manifest_file = tmp_path / "browser_manifest.txt"
    manifest_file.write_text(
        "Profile 2\nhttps://example.com/1\n",
        encoding="utf-8",
    )

    plan = build_random_favs_browser_launch_plan(
        manifest_file,
        shortcut_target=r"C:\Chrome\chrome.exe",
        shortcut_work_dir=r"C:\Chrome",
        shortcut_args="",
    )

    assert '"https://example.com/1"' in plan.cmd
    assert "file:///" not in plan.cmd
