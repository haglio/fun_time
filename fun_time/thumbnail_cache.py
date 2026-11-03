from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import cv2
from PIL import Image

from .folder_listings import FolderListings

DEFAULT_MAX_SIZE = 160

THUMBNAIL_CACHE_DIRNAME = "hud_thumbnails"


def thumbnail_path(
    video_path: str | Path, cache_dir: str | Path, listings: FolderListings | None = None
) -> Path:
    spelled = os.path.abspath(video_path)
    dated = listings or FolderListings()
    digest = hashlib.sha1(f"{spelled}|{int(dated.modified(spelled))}".encode()).hexdigest()[:16]
    return Path(cache_dir) / f"{digest}.jpg"


def cached_thumbnail(
    video_path: str | Path, cache_dir: str | Path, listings: FolderListings | None = None
) -> Path | None:
    dest = thumbnail_path(video_path, cache_dir, listings)
    return dest if dest.is_file() else None


_FIFTHS = 5


def _read_representative_frame(video_path: str | Path):
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            return None
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        target = total // _FIFTHS if total > _FIFTHS else 0
        if target:
            capture.set(cv2.CAP_PROP_POS_FRAMES, target)
        ok, frame = capture.read()
        if not ok:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = capture.read()
        return frame if ok else None
    finally:
        capture.release()


def thumbnail_for(
    video_path: str | Path, cache_dir: str | Path, max_size: int = DEFAULT_MAX_SIZE,
    listings: FolderListings | None = None,
) -> Path | None:
    dest = thumbnail_path(video_path, cache_dir, listings)
    if dest.is_file():
        return dest
    frame = _read_representative_frame(video_path)
    if frame is None:
        return None
    image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    image.thumbnail((max_size, max_size))
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "JPEG", quality=80)
    return dest


def prewarm_thumbnails(
    videos: Iterable[str | Path],
    cache_dir: str | Path,
    thumbnailer: Callable[[str | Path, str | Path], object] = thumbnail_for,
    sleep_fn: Callable[[float], None] = time.sleep,
    pause_s: float = 0.05,
) -> None:
    for video in videos:
        thumbnailer(video, cache_dir)
        sleep_fn(pause_s)
