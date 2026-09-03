from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChromeShortcut:
    """A resolved Chrome shortcut: target, start-in directory, baked-in args."""

    target: str
    work_dir: str
    args: str


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


def _command_opening(shortcut: ChromeShortcut) -> str:
    """The quoted target plus the shortcut's own arguments."""
    cmd = _quote(shortcut.target)
    existing_args = shortcut.args.strip()
    if existing_args:
        cmd += f" {existing_args}"
    return cmd


def build_random_favs_browser_launch_plan(
    manifest_path: str | Path, *, shortcut: ChromeShortcut
) -> RandomFavsBrowserLaunchPlan:
    manifest = read_random_favs_browser_manifest(manifest_path)
    if not shortcut.target or not manifest.urls:
        return RandomFavsBrowserLaunchPlan(should_launch=False, cmd="", work_dir="")

    cmd = _command_opening(shortcut)
    lowered = shortcut.args.strip().lower()
    if manifest.profile_dir and "--profile-directory" not in lowered:
        cmd += f" --profile-directory={_quote(manifest.profile_dir)}"
    if "--new-window" not in lowered:
        cmd += " --new-window"

    for url in manifest.urls:
        cmd += f" {_quote(url)}"
    return RandomFavsBrowserLaunchPlan(
        should_launch=True,
        cmd=cmd,
        work_dir=shortcut.work_dir,
    )


def launch_random_favs_browser(
    manifest_path: str | Path, *, shortcut: ChromeShortcut
) -> RandomFavsBrowserLaunchPlan:
    plan = build_random_favs_browser_launch_plan(manifest_path, shortcut=shortcut)
    if plan.should_launch and plan.cmd:
        subprocess.Popen(plan.cmd, cwd=plan.work_dir)
    return plan


def build_open_rfb_tab_command(*, urls: list[str], shortcut: ChromeShortcut) -> str:
    """Build ONE Chrome command opening every URL as a tab in the RFB profile:
    launching chrome.exe once per URL in quick succession races its singleton
    and silently drops tabs (the "lock both" bug)."""
    cmd = _command_opening(shortcut)
    for url in urls:
        cmd += f" {_quote(url)}"
    return cmd


def open_rfb_tab(*, urls: list[str], shortcut: ChromeShortcut) -> None:
    """Open one or more URLs as tabs in the RFB Chrome window, in one launch."""
    cmd = build_open_rfb_tab_command(urls=urls, shortcut=shortcut)
    subprocess.Popen(cmd, cwd=shortcut.work_dir)


def _quote(value: str) -> str:
    return f'"{value}"'
