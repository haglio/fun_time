from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from app_support.video_names import version_group_of
from funestra.playlist import PlaylistItem

from fun_time.media_metadata import recorded_for

from .discovery import discover_entries
from .library import LibraryEntry, group_versions, version_index_from_groups

PHASE_DISCOVER = "discover"


@dataclass(frozen=True)
class LibrarySource:
    entries: list[LibraryEntry]
    metadata_root: Path | None = None

    @cached_property
    def records(self) -> dict[Path, dict]:
        return {entry.video: recorded_for(entry.video, self.metadata_root) for entry in self.entries}

    def _group_id_of(self) -> Callable[[Path], str | None] | None:
        if self.metadata_root is None:
            return None
        records = self.records
        return lambda video: version_group_of(records[video]) or None

    @cached_property
    def version_index(self) -> dict[Path, list[PlaylistItem]]:
        return version_index_from_groups(
            group_versions(self.entries, self._group_id_of())
        )


def build_library_source(
    videos_dir: Path,
    *,
    metadata_root: Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> LibrarySource:
    if on_progress is not None:
        on_progress(PHASE_DISCOVER)
    return LibrarySource(entries=discover_entries(videos_dir, metadata_root=metadata_root),
                         metadata_root=metadata_root)
