"""A video's size and date as its folder lists them, never asked of the video:
pCloud lists a folder at once and can take hours over one file in it."""
from __future__ import annotations

import os
from pathlib import Path


class FolderListings:
    """Each folder read once, for one drawing or one build."""

    def __init__(self) -> None:
        self._read: dict[str, dict[str, os.stat_result]] = {}

    def size(self, video: str | Path) -> int:
        listed = self._listed(video)
        return 0 if listed is None else listed.st_size

    def modified(self, video: str | Path) -> float:
        listed = self._listed(video)
        return 0.0 if listed is None else listed.st_mtime

    def _listed(self, video: str | Path) -> os.stat_result | None:
        folder, name = os.path.split(os.path.abspath(video))
        key = os.path.normcase(folder)
        if key not in self._read:
            self._read[key] = _listing_of(folder)
        return self._read[key].get(os.path.normcase(name))


def _listing_of(folder: str) -> dict[str, os.stat_result]:
    try:
        with os.scandir(folder) as listing:
            return {
                os.path.normcase(entry.name): entry.stat(follow_symlinks=False)
                for entry in listing
            }
    except OSError:
        return {}
