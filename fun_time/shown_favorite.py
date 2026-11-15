from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from .gallery import Gallery
from .media_metadata import a_favorite

RECHECK_S = 2.0


class ShownFavorite:
    def __init__(self, *, metadata_root: Path | None, gallery: Callable[[], Gallery],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._metadata_root = metadata_root
        self._gallery = gallery
        self._clock = clock
        self._read: tuple[str, bool] | None = None
        self._read_at = 0.0
        self._favorite = False

    def of(self, item: str, *, held: bool) -> bool:
        if not item:
            return False
        now = self._clock()
        if self._read != (item, held) or now - self._read_at >= RECHECK_S:
            self._favorite = a_favorite(item, self._metadata_root, self._gallery)
            self._read, self._read_at = (item, held), now
        return self._favorite
