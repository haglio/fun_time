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

# The main player's per-video durations, and the one thing here that is merged rather than
# copied.  Startup waits for the main player to report the video it is opening, the main player reports
# nothing until it has a duration for it, and against a cold cache it goes off
# and probes the whole library first — measured at 20s where a warm one is 0.1s.
DURATION_CACHE_NAME = "main_player_durations.json"


def mirror_private_overlays(primary: Path, worktree: Path) -> list[Path]:
    """Copy into *worktree* the git-ignored overlays a session reads from its
    own checkout, and return what was copied.

    ``content.local.json`` (the real filter vocabulary) and the Provider
    autofill userscript are private overlays: git-ignored, so they sit in the
    primary checkout and in no worktree, and each is found relative to the
    package that reads it rather than through config.  A branch session without
    them falls back to the committed placeholders — the library browser's
    filters come up as ``alpha``/``beta``/``gamma`` — and the user is looking at
    a wrongness their branch did not cause.  Refreshed on every launch, so an
    edit to the real one is never a stale copy away.

    ``fun_time_config.json`` is deliberately not among them: the branch config
    written under ``state/`` is the whole point, and a copy of the real one
    loose in a worktree is a live config that nobody passes ``--config``.
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
    """The library-derived files copied across whole.

    These two are cost paid after startup rather than during it: without them
    the HUD maps fill in one decoded frame at a time and the breeding view comes
    up empty — a wrongness the branch did not cause, which is exactly what a
    verification session must never show.  The duration cache, which is the one
    that costs *startup*, is merged instead.
    """
    from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME  # noqa: PLC0415  (pulls in cv2)

    return ("watch_stats.json", THUMBNAIL_CACHE_DIRNAME)


def merge_duration_cache(live_state: Path, branch_state: Path) -> int:
    """Union the live session's durations into the branch's; return the total.

    Copying this one was wrong, and copy-if-newer — what it was — was wrong in
    the way that hides: the main player rewrites the file with exactly what it loaded plus
    what it probed, so a branch session's copy shrinks to its own view of the
    library and is then *newer* than the live session's.  Every launch after the
    first therefore skipped the seed, kept the small file, and re-probed
    whatever the library had churned since — which is why branch launches went
    on taking half a minute after the seeding landed.

    The two files are partial views of one library, so the union is strictly
    better than either.  The branch's own readings win on conflict, being the
    more recent observation of that file; everything the live session knows and
    the branch does not comes across.  A stale entry costs nothing either way —
    every entry is validated against the file's mtime and size before it is
    trusted, and re-probed when it does not match.
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
    unfinished code, and the live session's caches are not its to corrupt.  For
    the copied ones a file already in the branch state dir and newer than the
    live one is left alone — that is the branch session's own work, and this is
    a seed rather than a sync.  The duration cache cannot work that way and is
    merged; :func:`merge_duration_cache` says why.
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
    """Copy the entries *destination* does not have yet.

    Only the missing ones: a cache keyed by content — a thumbnail is named for
    its video and that video's modification time — never needs refreshing, so
    every launch after the first copies nothing.
    """
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_file() and not target.exists():
            shutil.copyfile(entry, target)
            copied.append(target)
    return copied
