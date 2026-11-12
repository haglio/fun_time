from __future__ import annotations

import json
from pathlib import Path

from app_support.video_names import stem_as_made


class GalleryNames:
    def __init__(self, published: Path | None) -> None:
        self._published = published
        self._read_at: int | None = None
        self._names: dict[str, str] = {}

    def of(self, video: Path) -> str:
        self._take_what_is_published()
        return self._names.get(stem_as_made(video), "")

    def _take_what_is_published(self) -> None:
        if self._published is None:
            return
        try:
            written_at = self._published.stat().st_mtime_ns
            if written_at == self._read_at:
                return
            names = json.loads(self._published.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(names, dict):
            self._names, self._read_at = names, written_at


NO_GALLERY_NAMES = GalleryNames(None)
