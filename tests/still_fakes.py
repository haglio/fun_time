"""Stand-ins for extracting the library browser's stills off its tests' made-up videos."""
from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image

from fun_time.thumbnail_cache import thumbnail_path


def still_extractions() -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == "library-thumbnails"]


class ExtractionsHeldAtTheFirst:
    """Each extraction leaves a still where the cache keeps it, and the first one
    waits until the test lets it go."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.holding = threading.Event()
        self.let_go = threading.Event()

    def __call__(self, video: str, cache: Path, *, listings=None) -> None:
        self.asked.append(video)
        if len(self.asked) == 1:
            self.holding.set()
            self.let_go.wait(10.0)
        still = thumbnail_path(video, cache, listings)
        still.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 9)).save(still, "JPEG")

    def let_every_extraction_end(self) -> None:
        self.let_go.set()
        for extraction in still_extractions():
            extraction.join(10.0)
