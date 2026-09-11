from __future__ import annotations

from pathlib import Path

from player_core.clip_folder import SUPPORTED_VIDEO_EXTS

from .library import LibraryEntry


def discover_entries(videos_dir: Path, scripts_dir: Path) -> list[LibraryEntry]:
    """Pair every video under *videos_dir* with its funscript and file size.

    Videos without a matching funscript get ``funscript=None`` — they play
    normally; with no script the OSR2 rests at its parked position and loop
    recording falls back to raw clip ranges. File size is read here so the
    library layer can pick the largest of several versions without re-stat'ing.
    Order is stable (sorted); randomization is the library layer's job.
    """
    entries: list[LibraryEntry] = []
    for video in sorted(videos_dir.rglob("*")):
        if not video.is_file() or video.suffix.lower() not in SUPPORTED_VIDEO_EXTS:
            continue
        relative = video.relative_to(videos_dir)
        funscript = scripts_dir / relative.with_suffix(".funscript")
        entries.append(
            LibraryEntry(
                video=video,
                funscript=funscript if funscript.exists() else None,
                size=video.stat().st_size,
            )
        )
    return entries
