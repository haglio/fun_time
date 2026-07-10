"""Build Provider regenerate URLs from a video's metadata sidecar.

The gallery page an AI video came from is usually gone, so Fun Time points at
the Provider generate page instead, with the original prompts/settings packed into
the URL fragment (``#ft=<json>``). A userscript on example.com reads the fragment,
fills the form, and raises a floating note with the settings it could not set.
Videos made from a source image target the image page (``/create``);
text-to-video targets the video page (``/video``).

Both the lock hotkey and the Random Favs Browser open their tabs this way.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from .media_metadata import load_metadata, metadata_path_for

# (label, metadata key) in display order for the floating note / auto-fill.
_IMAGE_SETTINGS = [
    ("Model", "model"),
    ("Resolution", "resolution"),
    ("Aspect ratio", "aspect_ratio"),
    ("Quality", "quality"),
    ("Style", "style"),
    ("Creativity", "creativity"),
    ("Seed", "seed"),
    ("Created", "created"),
]
_VIDEO_SETTINGS = [
    ("Model", "model"),
    ("Action", "action"),
    ("Resolution", "resolution"),
    ("Aspect ratio", "aspect_ratio"),
    ("Quality", "quality"),
    ("Seed", "seed"),
    ("Created", "created"),
]


def _settings_pairs(block: dict, keys: list[tuple[str, str]]) -> list[list[str]]:
    return [[label, str(block[key])] for label, key in keys if block.get(key)]


def build_payload(metadata: dict) -> dict:
    """Build the ``#ft`` payload the userscript consumes."""
    video = metadata.get("video") or {}
    source = metadata.get("source_image") or None
    if source:
        return {
            "kind": "image",
            "positive": source.get("positive_prompt", ""),
            "negative": source.get("negative_prompt", ""),
            "settings": _settings_pairs(source, _IMAGE_SETTINGS),
            "video_prompt": video.get("prompt", ""),
            "video_settings": _settings_pairs(video, _VIDEO_SETTINGS),
        }
    return {
        "kind": "video",
        "positive": video.get("prompt", ""),
        "negative": "",
        "settings": _settings_pairs(video, _VIDEO_SETTINGS),
        "video_prompt": "",
        "video_settings": [],
    }


def build_regen_url(metadata: dict, *, video_url: str, image_url: str) -> str:
    payload = build_payload(metadata)
    base = image_url if payload["kind"] == "image" else video_url
    encoded = quote(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return f"{base}#ft={encoded}"


def regen_url_for_video(
    video_path: str | Path,
    *,
    media_root: str | Path | None,
    metadata_root: str | Path | None,
    video_url: str,
    image_url: str,
) -> str:
    """Return the Provider regenerate URL for a Provider video, or "" if it has none."""
    if "provider" not in str(video_path).lower():
        return ""
    meta_path = metadata_path_for(video_path, media_root, metadata_root)
    if meta_path is None or not meta_path.is_file():
        return ""
    metadata = load_metadata(meta_path)
    if not metadata.get("video"):
        return ""
    return build_regen_url(metadata, video_url=video_url, image_url=image_url)
