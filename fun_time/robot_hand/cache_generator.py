from __future__ import annotations

from pathlib import Path

from .frame_cache import write_rhcache
from .video import decode_video_to_numpy_frames


def ensure_clip_cached(
    video_path: Path,
    cache_dir: Path,
    *,
    quality: int = 95,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / (video_path.stem + ".rhcache")
    if cache_path.exists():
        return cache_path

    frames = decode_video_to_numpy_frames(video_path)
    write_rhcache(frames, cache_path, source_name=video_path.name, quality=quality)
    return cache_path
