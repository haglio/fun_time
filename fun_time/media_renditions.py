# Chrome decodes HEVC only through a platform decoder, and every Chrome plays the H.264 originals,
# so a preview shows the original.
from __future__ import annotations

from pathlib import Path

_UPSCALED_ROOT = ("2_outbox", "upscaled_by_orientation")
_ORIGINAL_ROOT = "1_sorted"
_UPSCALE_SUFFIX = "_topaz"


def original_rendition(video_path: str | Path, media_root: str | Path | None) -> str:
    if not video_path or media_root is None:
        return ""
    video = Path(video_path)
    try:
        relative = video.relative_to(media_root)
    except ValueError:
        return ""

    parts = relative.parts
    if parts[: len(_UPSCALED_ROOT)] != _UPSCALED_ROOT or len(parts) != len(_UPSCALED_ROOT) + 3:
        return ""
    orientation, source = parts[len(_UPSCALED_ROOT)], parts[len(_UPSCALED_ROOT) + 1]

    stem = video.stem
    stem = stem.removesuffix(_UPSCALE_SUFFIX)
    original = Path(media_root) / _ORIGINAL_ROOT / source / orientation / (stem + video.suffix)
    return str(original) if original.is_file() else ""


def renditions(video_path: str | Path, media_root: str | Path | None) -> list[str]:
    original = original_rendition(video_path, media_root)
    return [str(video_path), original] if original else []
