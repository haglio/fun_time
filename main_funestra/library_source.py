"""The library Kino knows the Main player's videos by: which files are versions
of one video, and the clips a compilation was carved into."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from funestra.playlist import PlaylistItem

from fun_time.media_metadata import recorded_for, version_group_of

from .discovery import discover_entries
from .library import LibraryEntry, group_versions, version_index_from_groups

# The wait a caller can cover with a loading screen.  Naming the phase (rather
# than passing display text) keeps the wording where the screen is.
PHASE_DISCOVER = "discover"


@dataclass(frozen=True)
class LibrarySource:
    entries: list[LibraryEntry]
    # When set, version families come from Evolver's metadata sidecars (the
    # authoritative record) instead of the Main Funestra's own name-prefix guess.
    metadata_root: Path | None = None

    def _group_id_of(self) -> Callable[[Path], str | None] | None:
        if self.metadata_root is None:
            return None
        metadata_root = self.metadata_root
        return lambda video: version_group_of(recorded_for(video, metadata_root)) or None

    @cached_property
    def version_index(self) -> dict[Path, list[PlaylistItem]]:
        """Version-cycle map over every video (main entries and clips).

        Uses the metadata sidecars when a *metadata_root* is set, else names.
        Cached because the answer is already fixed for the session: the two
        readers are both in startup, and what the session is handed is a plain
        dict it holds until it closes.
        """
        return version_index_from_groups(
            group_versions(self.entries, self._group_id_of())
        )


def build_library_source(
    videos_dir: Path,
    scripts_dir: Path,
    *,
    metadata_root: Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> LibrarySource:
    """Discover videos + clips.  *metadata_root*, when given, makes version
    grouping read Evolver's sidecars instead of guessing from names.

    *on_progress* is called with the phase before the walk.  It may raise to
    abort the build; nothing here catches that, which is how the loading screen
    turns a closed window into an immediate exit.
    """
    if on_progress is not None:
        on_progress(PHASE_DISCOVER)
    return LibrarySource(entries=discover_entries(videos_dir, scripts_dir),
                         metadata_root=metadata_root)
