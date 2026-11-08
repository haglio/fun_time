from __future__ import annotations

import shutil
from pathlib import Path

PRIVATE_OVERLAYS = (
    Path("content.local.json"),
    Path("fun_time") / "static" / "regen_autofill.user.js",
)

WATCH_STATS_NAME = "watch_stats.json"


def mirror_private_overlays(primary: Path, worktree: Path) -> list[Path]:
    copied: list[Path] = []
    for relative in PRIVATE_OVERLAYS:
        source = primary / relative
        if not source.is_file():
            continue
        destination = worktree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append(destination)
    return copied


def seed_what_is_costly_to_rebuild(live_state: Path, branch_state: Path) -> list[Path]:
    seeded: list[Path] = []
    source = live_state / WATCH_STATS_NAME
    if source.is_file() and _seed_file(source, branch_state / WATCH_STATS_NAME):
        seeded.append(branch_state / WATCH_STATS_NAME)
    return seeded


def top_up_thumbnails(live_state: Path, branch_state: Path) -> list[Path]:
    from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME  # noqa: PLC0415  (pulls in cv2)

    source = live_state / THUMBNAIL_CACHE_DIRNAME
    if not source.is_dir():
        return []
    destination = branch_state / THUMBNAIL_CACHE_DIRNAME
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_file() and not target.exists() and _copy_unless_made_meanwhile(entry, target):
            copied.append(target)
    return copied


def _seed_file(source: Path, destination: Path) -> bool:
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return True


def _copy_unless_made_meanwhile(source: Path, target: Path) -> bool:
    partial = target.with_name(f"{target.stem}.partial.seed")
    shutil.copyfile(source, partial)
    try:
        partial.rename(target)
    except FileExistsError:
        partial.unlink()
        return False
    return True
