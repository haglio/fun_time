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


def _seed_file(source: Path, destination: Path) -> bool:
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return True
