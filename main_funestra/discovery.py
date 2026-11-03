from __future__ import annotations

from pathlib import Path

from funestra.flick_folder import SUPPORTED_VIDEO_EXTS

from fun_time.modes import scripted_item

from .library import LibraryEntry


def discover_entries(videos_dir: Path, *, metadata_root: Path | None) -> list[LibraryEntry]:
    entries: list[LibraryEntry] = []
    for video in sorted(videos_dir.rglob("*")):
        if not video.is_file() or video.suffix.lower() not in SUPPORTED_VIDEO_EXTS:
            continue
        entries.append(
            LibraryEntry(
                video=video,
                funscript=scripted_item(str(video), metadata_root).funscript,
                size=video.stat().st_size,
            )
        )
    return entries
