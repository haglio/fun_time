"""The last listing of the library, kept so the next browse opens on it at
once -- listing folders is instant locally and minutes while syncing, and the
browse waits on it although the answer barely changes browse to browse."""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

_VIDEOS = "videos"


def remembered_listing(
    kept: Path, read: Callable[[], list[str]], *, afresh: bool = False,
) -> list[str]:
    """The videos to browse: what was kept, else what *read* finds now.

    *afresh* reads past whatever was kept, which is how it catches up with a
    library that has changed.
    """
    if not afresh:
        videos = _kept(kept)
        if videos is not None:
            return videos
    found = read()
    if found or _kept(kept) is None:
        _keep(kept, found)
    return found


def _kept(kept: Path) -> list[str] | None:
    try:
        payload = json.loads(kept.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    videos = payload.get(_VIDEOS) if isinstance(payload, dict) else None
    if not isinstance(videos, list) or not all(isinstance(video, str) for video in videos):
        return None
    return videos


def _keep(kept: Path, videos: list[str]) -> None:
    try:
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps({_VIDEOS: videos}, indent=2) + "\n", encoding="utf-8")
    except OSError:  # a browse that cannot remember still browses
        logger.warning("Could not keep the library listing at %s", kept, exc_info=True)
