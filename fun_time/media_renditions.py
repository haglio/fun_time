"""Locate the pre-upscale rendition of a library video.

Every video in the library exists twice.  The original the site produced lands
under ``1_sorted/<source>/<orientation>/<name>.mp4``; Topaz then upscales it to
``2_outbox/upscaled_by_orientation/<orientation>/<source>/<name>_topaz.mp4``,
which is what plays and what a favourite records.

The two renditions are not interchangeable for a browser.  The upscales are
hundreds of megabytes of 1080p-plus HEVC, which Chrome decodes only through a
platform decoder; the originals are a couple of megabytes of H.264, which any
Chrome plays.  Anything that wants to *show* a library video rather than play it
full-size — a thumbnail, a preview — wants the original.
"""
from __future__ import annotations

from pathlib import Path

_UPSCALED_ROOT = ("2_outbox", "upscaled_by_orientation")
_ORIGINAL_ROOT = "1_sorted"
_UPSCALE_SUFFIX = "_topaz"


def original_rendition(video_path: str | Path, media_root: str | Path | None) -> str:
    """The original *video_path* was upscaled from, or "" when there is none.

    Returns "" for a video outside the upscale tree, or whose original is not on
    disk — callers fall back to the video they already have.
    """
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
    if stem.endswith(_UPSCALE_SUFFIX):
        stem = stem[: -len(_UPSCALE_SUFFIX)]
    # The upscale tree nests orientation/source; the sorted tree nests the other way.
    original = Path(media_root) / _ORIGINAL_ROOT / source / orientation / (stem + video.suffix)
    return str(original) if original.is_file() else ""
