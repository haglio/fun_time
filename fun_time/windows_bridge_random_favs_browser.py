from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .shortcuts import Shortcut

# Matched as a substring of the window class, which is "Chrome_WidgetWin_1".
CHROME_WINDOW_CLASS = "Chrome"


@dataclass(frozen=True)
class RandomFavsBrowserManifest:
    profile_dir: str
    urls: list[str]


@dataclass(frozen=True)
class RandomFavsBrowserLaunchPlan:
    should_launch: bool
    cmd: str
    work_dir: str


def read_random_favs_browser_manifest(path: str | Path) -> RandomFavsBrowserManifest:
    manifest_path = Path(path)
    if not manifest_path.is_file():
        return RandomFavsBrowserManifest(profile_dir="", urls=[])

    content = manifest_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    if not lines:
        return RandomFavsBrowserManifest(profile_dir="", urls=[])

    profile_dir = lines[0]
    urls = [line for line in lines[1:] if line]
    return RandomFavsBrowserManifest(profile_dir=profile_dir, urls=urls)


def build_random_favs_browser_launch_plan(
    manifest_path: str | Path, *, shortcut: Shortcut
) -> RandomFavsBrowserLaunchPlan:
    manifest = read_random_favs_browser_manifest(manifest_path)
    if not shortcut.target or not manifest.urls:
        return RandomFavsBrowserLaunchPlan(should_launch=False, cmd="", work_dir="")

    lowered = shortcut.arguments.strip().lower()
    arguments = []
    if manifest.profile_dir and "--profile-directory" not in lowered:
        arguments.append(f"--profile-directory={manifest.profile_dir}")
    if "--new-window" not in lowered:
        arguments.append("--new-window")
    return RandomFavsBrowserLaunchPlan(
        should_launch=True,
        cmd=shortcut.command_line(*arguments, *manifest.urls),
        work_dir=shortcut.work_dir,
    )


def launch_random_favs_browser(
    manifest_path: str | Path, *, shortcut: Shortcut
) -> RandomFavsBrowserLaunchPlan:
    plan = build_random_favs_browser_launch_plan(manifest_path, shortcut=shortcut)
    if plan.should_launch and plan.cmd:
        subprocess.Popen(plan.cmd, cwd=plan.work_dir)
    return plan


def build_open_rfb_tab_command(*, urls: list[str], shortcut: Shortcut) -> str:
    """ONE Chrome command opening every URL as a tab in the RFB profile."""
    return shortcut.command_line(*urls)


def open_rfb_tab(*, urls: list[str], shortcut: Shortcut) -> None:
    """Open one or more URLs as tabs in the RFB Chrome window, in one launch."""
    cmd = build_open_rfb_tab_command(urls=urls, shortcut=shortcut)
    subprocess.Popen(cmd, cwd=shortcut.work_dir)
