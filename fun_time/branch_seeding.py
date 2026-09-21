"""What a branch session starts from the live session's state, and what it does not.

A worktree session gets its own ``state/`` so a half-finished branch cannot
corrupt what the live session reads back.  The few files in there that describe
the *library* rather than the session come across anyway, because rebuilding
them from cold is work already done against thousands of files.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

# Git-ignored overlays that a session reads from its own checkout, so they exist
# in the primary and in no worktree.  See :func:`mirror_private_overlays`.
PRIVATE_OVERLAYS = (
    Path("content.local.json"),
    Path("fun_time") / "static" / "regen_autofill.user.js",
)

# The main player's per-video durations, merged rather than copied.  Startup waits
# for it to report the video it is opening, it reports nothing until it has that
# video's duration, and against a cold cache it probes the whole library first:
# measured at 20s where a warm one is 0.1s.
DURATION_CACHE_NAME = "main_player_durations.json"


def mirror_private_overlays(primary: Path, worktree: Path) -> list[Path]:
    """Copy into *worktree* the git-ignored overlays a session reads from its
    own checkout, and return what was copied.

    Each is found relative to the package that reads it rather than through
    config, so a branch session without them falls back to the committed
    placeholders and he is looking at a wrongness his branch did not cause.
    """
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


def _seeded_state_names() -> tuple[str, ...]:
    """The library-derived files copied across whole: cost paid after startup
    rather than during it, where the duration cache above costs startup."""
    from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME  # noqa: PLC0415  (pulls in cv2)

    return ("watch_stats.json", THUMBNAIL_CACHE_DIRNAME)


def merge_duration_cache(live_state: Path, branch_state: Path) -> int:
    """Union the live session's durations into the branch's; return the total.

    Copy-if-newer cannot work here: the main player rewrites the file with what
    it loaded plus what it probed, so a branch's copy shrinks to its own view of
    the library and is then newer than the live session's.  Both are partial
    views of one library, and a stale entry costs nothing -- every entry is
    checked against the file's mtime and size before it is trusted.
    """
    live = _read_json_dict(live_state / DURATION_CACHE_NAME)
    branch = _read_json_dict(branch_state / DURATION_CACHE_NAME)
    merged = {**live, **branch}
    if merged and merged != branch:
        branch_state.mkdir(parents=True, exist_ok=True)
        (branch_state / DURATION_CACHE_NAME).write_text(json.dumps(merged), encoding="utf-8")
    return len(merged)


def _read_json_dict(path: Path) -> dict:
    """*path* as a dict, or empty when it is missing, unreadable or not one."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def seed_derived_caches(live_state: Path, branch_state: Path) -> list[Path]:
    """Start the library-derived caches from *live_state*; return what landed.

    Copied rather than hardlinked, and never written back: a branch is
    unfinished code, and the live session's caches are not its to corrupt.
    """
    seeded: list[Path] = []
    if merge_duration_cache(live_state, branch_state):
        seeded.append(branch_state / DURATION_CACHE_NAME)
    for name in _seeded_state_names():
        source = live_state / name
        if source.is_dir():
            seeded.extend(_seed_directory(source, branch_state / name))
        elif source.is_file() and _seed_file(source, branch_state / name):
            seeded.append(branch_state / name)
    return seeded


def _seed_file(source: Path, destination: Path) -> bool:
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return True


def _seed_directory(source: Path, destination: Path) -> list[Path]:
    """Copy the entries *destination* does not have yet: a thumbnail is named for
    its video and that video's modification time, so it never needs refreshing."""
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_file() and not target.exists():
            shutil.copyfile(entry, target)
            copied.append(target)
    return copied
