from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

_STATIC_DIR = Path(__file__).resolve().parent / "static"


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


def tab_placeholder_path() -> Path:
    """Return the path to the lazy-load tab placeholder HTML file."""
    return _STATIC_DIR / "tab_placeholder.html"


def _wrap_url_with_placeholder(url: str, placeholder: Path) -> str:
    """Convert a URL into a file:// URI that opens the placeholder page."""
    file_uri = placeholder.as_uri()
    return f"{file_uri}?url={quote(url, safe='')}"


def build_random_favs_browser_launch_plan(
    manifest_path: str | Path,
    *,
    shortcut_target: str,
    shortcut_work_dir: str,
    shortcut_args: str,
    placeholder_path: str | Path | None = None,
) -> RandomFavsBrowserLaunchPlan:
    manifest = read_random_favs_browser_manifest(manifest_path)
    if not shortcut_target or not manifest.urls:
        return RandomFavsBrowserLaunchPlan(should_launch=False, cmd="", work_dir="")

    cmd = _quote(shortcut_target)
    existing_args = shortcut_args.strip()
    if existing_args:
        cmd += f" {existing_args}"
    lowered = existing_args.lower()
    if manifest.profile_dir and "--profile-directory" not in lowered:
        cmd += f" --profile-directory={_quote(manifest.profile_dir)}"
    if "--new-window" not in lowered:
        cmd += " --new-window"

    placeholder = Path(placeholder_path) if placeholder_path else None
    for url in manifest.urls:
        if placeholder:
            cmd += f" {_quote(_wrap_url_with_placeholder(url, placeholder))}"
        else:
            cmd += f" {_quote(url)}"
    return RandomFavsBrowserLaunchPlan(
        should_launch=True,
        cmd=cmd,
        work_dir=shortcut_work_dir,
    )


def launch_random_favs_browser(
    manifest_path: str | Path,
    *,
    shortcut_target: str,
    shortcut_work_dir: str,
    shortcut_args: str,
    placeholder_path: str | Path | None = None,
) -> RandomFavsBrowserLaunchPlan:
    plan = build_random_favs_browser_launch_plan(
        manifest_path,
        shortcut_target=shortcut_target,
        shortcut_work_dir=shortcut_work_dir,
        shortcut_args=shortcut_args,
        placeholder_path=placeholder_path,
    )
    if plan.should_launch and plan.cmd:
        subprocess.Popen(plan.cmd, cwd=plan.work_dir)
    return plan


def _quote(value: str) -> str:
    return f'"{value}"'
