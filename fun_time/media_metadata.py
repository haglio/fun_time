"""Generation-metadata sidecar access for AI videos.

Every AI video under the provider media root may have a JSON sidecar in a
mirrored tree under the metadata root, recording the prompts and settings it
was generated from (a ``video`` block, plus a ``source_image`` block when the
video was animated from a generated image).  This module owns the mapping
from a video file to its sidecar and the sidecar loading; consumers layer
their own interpretation on top (e.g. :mod:`fun_time.provider_regen` builds
regenerate URLs from it).
"""
from __future__ import annotations

import json
from pathlib import Path


def _norm(path: str | Path) -> Path:
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


def metadata_path_for(
    video_path: str | Path,
    media_root: str | Path | None,
    metadata_root: str | Path | None,
) -> Path | None:
    """Map a video file under *media_root* to its metadata JSON under *metadata_root*."""
    if media_root is None or metadata_root is None:
        return None
    try:
        rel = _norm(video_path).relative_to(_norm(media_root))
    except ValueError:
        return None
    return Path(metadata_root) / rel.with_suffix(".json")


def load_metadata(json_path: str | Path) -> dict:
    try:
        with open(json_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}
