"""Disk-cached still frames for videos, for the lock-status HUD.

Videos are raw ``.mp4`` files with no sidecar images, so a representative
frame is extracted once with OpenCV and cached to disk. The cache filename
folds in the source file's modification time, so a replaced clip at the same
path yields a fresh thumbnail instead of a stale one.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from pathlib import Path

import cv2
from PIL import Image

# Default longest-edge size (px) for a HUD sibling thumbnail.
DEFAULT_MAX_SIZE = 160

# Where the cache lives, under the session's state dir.  One cache for every
# still in the session — the satellites' HUD maps and the library browser's
# tiles — so a video decoded for one is already warm for the other.
THUMBNAIL_CACHE_DIRNAME = "hud_thumbnails"


def _norm(path: str | Path) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


def thumbnail_path(video_path: str | Path, cache_dir: str | Path) -> Path:
    """Deterministic cache location for *video_path*'s thumbnail.

    The name folds in the resolved path and the file's mtime, so the same clip
    maps to the same file until it is modified, then to a new one.
    """
    resolved = _norm(video_path)
    try:
        mtime = int(resolved.stat().st_mtime)
    except OSError:
        mtime = 0
    digest = hashlib.sha1(f"{resolved}|{mtime}".encode()).hexdigest()[:16]
    return Path(cache_dir) / f"{digest}.jpg"


def cached_thumbnail(video_path: str | Path, cache_dir: str | Path) -> Path | None:
    """The cached thumbnail for *video_path* if it already exists, else None —
    never extracting one.  The HUD paints with this so its refresh never blocks
    on a cv2 frame grab (seconds, for HEVC); the background prewarm does the
    extracting, and the next refresh picks the file up."""
    dest = thumbnail_path(video_path, cache_dir)
    return dest if dest.is_file() else None


def _read_representative_frame(video_path: str | Path):
    """A frame ~20% into the clip (skipping fade-ins), or the first readable one."""
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            return None
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        target = total // 5 if total > 5 else 0
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
    video_path: str | Path, cache_dir: str | Path, max_size: int = DEFAULT_MAX_SIZE
) -> Path | None:
    """Path to *video_path*'s cached thumbnail, extracting it on first use.

    Returns ``None`` when the video cannot be opened or has no readable frame.
    A previously cached thumbnail is reused without touching the video.
    """
    dest = thumbnail_path(video_path, cache_dir)
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
    """Extract and cache *videos*' thumbnails, so a first use never blocks on a
    frame grab.  Idempotent — an already cached thumbnail is skipped.  Sleeps
    briefly between clips so decoding a big HEVC library never starves the
    session's own work (that starvation was showing up as multi-second blinks);
    run it off the main thread.

    The caller chooses which videos: a satellite warms every clip in its
    library, the library browser one per handle off its cheapest rendition.
    """
    for video in videos:
        thumbnailer(video, cache_dir)
        sleep_fn(pause_s)
