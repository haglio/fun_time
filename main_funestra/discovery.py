from __future__ import annotations

from pathlib import Path

from fun_time.folder_listings import FolderListings
from fun_time.modes import scripted_item, videos_in

from .library import LibraryEntry


def discover_entries(videos_dir: Path, *, metadata_root: Path | None) -> list[LibraryEntry]:
    sizes, scripts = FolderListings(), FolderListings()
    return [
        LibraryEntry(
            video=video,
            funscript=scripted_item(str(video), metadata_root, listings=scripts).funscript,
            size=sizes.size(video),
        )
        for video in sorted(Path(found) for found in videos_in([videos_dir]))
    ]
