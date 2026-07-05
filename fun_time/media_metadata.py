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
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


def normalize_path_key(path: str) -> str:
    return path.strip().lower()


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


def _norm_text(value: object) -> str:
    return " ".join(str(value or "").split()).lower()


# Fields that pin down the generated image (subject(s) + situation).  "created"
# is deliberately excluded: regenerating the identical config on another day
# yields the same picture.
_IMAGE_IDENTITY_FIELDS = (
    "positive_prompt",
    "negative_prompt",
    "model",
    "resolution",
    "aspect_ratio",
    "quality",
    "style",
    "creativity",
    "seed",
)

# Video-block fields shared by every variation axis; "action" and "seed" are
# appended per key kind ("created" excluded for the same reason as above).
_VIDEO_BASE_FIELDS = (
    "prompt",
    "model",
    "resolution",
    "aspect_ratio",
    "quality",
)


def _field_key(prefix: str, block: dict, fields: tuple[str, ...]) -> str:
    return prefix + "|" + "|".join(_norm_text(block.get(f)) for f in fields)


def action_group_key(metadata: dict) -> str | None:
    """Identity of the subject(s) + situation a video shows, or None if unknown.

    Videos sharing a key form an "action group": the same subject rendered
    doing different things.  For image-to-video clips the source image IS the
    subject, so the key is the image's full generation identity.  For
    text-to-video clips the subject is pinned by the video prompt + seed, so
    only the action dropdown is left free.
    """
    source = metadata.get("source_image")
    if source:
        return _field_key("img", source, _IMAGE_IDENTITY_FIELDS)
    video = metadata.get("video") or {}
    if not video.get("prompt"):
        return None
    return _field_key("t2v", video, _VIDEO_BASE_FIELDS + ("seed",))


_IMAGE_FAMILY_FIELDS = tuple(f for f in _IMAGE_IDENTITY_FIELDS if f != "seed")


def seed_group_key(metadata: dict) -> tuple[str, str] | None:
    """(family, seed) placing a video among its same-config-different-seed kin.

    Videos sharing a family were generated from the identical configuration
    with only the seed varied — the same scenario cast with a different subject.
    Returns None when the metadata lacks a seed or a prompt to family by.
    """
    source = metadata.get("source_image")
    if source:
        if not source.get("seed") or not source.get("positive_prompt"):
            return None
        return _field_key("img", source, _IMAGE_FAMILY_FIELDS), _norm_text(source.get("seed"))
    video = metadata.get("video") or {}
    if not video.get("seed") or not video.get("prompt"):
        return None
    return _field_key("t2v", video, _VIDEO_BASE_FIELDS + ("action",)), _norm_text(video.get("seed"))


@dataclass(frozen=True)
class GroupIndex:
    """Grouping of a video library by generation identity.

    Paths are keyed by :func:`normalize_path_key`; member lists hold the
    original path strings, sorted.  ``indexed_paths`` remembers every input
    path (sidecar or not) so callers can tell "no metadata" apart from
    "not indexed yet" when deciding whether a cached index is stale.
    """

    action_key_by_path: dict[str, str]
    action_members: dict[str, list[str]]
    seed_key_by_path: dict[str, tuple[str, str]]
    seed_members: dict[str, list[str]]
    indexed_paths: frozenset[str]

    def contains(self, path: str) -> bool:
        return normalize_path_key(path) in self.indexed_paths


def build_group_index(
    video_paths: Iterable[str],
    media_root: str | Path | None,
    metadata_root: str | Path | None,
) -> GroupIndex:
    """Index *video_paths* into action groups and seed families.

    Videos without a metadata sidecar are remembered (for staleness checks)
    but belong to no group.
    """
    action_key_by_path: dict[str, str] = {}
    action_members: dict[str, list[str]] = {}
    seed_key_by_path: dict[str, tuple[str, str]] = {}
    seed_members: dict[str, list[str]] = {}
    indexed: set[str] = set()
    for path in video_paths:
        indexed.add(normalize_path_key(path))
        sidecar = metadata_path_for(path, media_root, metadata_root)
        if sidecar is None or not sidecar.is_file():
            continue
        metadata = load_metadata(sidecar)
        action_key = action_group_key(metadata)
        if action_key is not None:
            action_key_by_path[normalize_path_key(path)] = action_key
            action_members.setdefault(action_key, []).append(path)
        seed_key = seed_group_key(metadata)
        if seed_key is not None:
            seed_key_by_path[normalize_path_key(path)] = seed_key
            seed_members.setdefault(seed_key[0], []).append(path)
    for members in action_members.values():
        members.sort()
    for members in seed_members.values():
        members.sort()
    return GroupIndex(
        action_key_by_path=action_key_by_path,
        action_members=action_members,
        seed_key_by_path=seed_key_by_path,
        seed_members=seed_members,
        indexed_paths=frozenset(indexed),
    )


# Sidecar scans cost ~1000 file reads per library, so indexes are cached per
# library key and rebuilt only when a probe path is missing — which is exactly
# what happens when a new arrival starts playing.
_INDEX_CACHE: dict[str, GroupIndex] = {}


def cached_group_index(
    cache_key: str,
    *,
    paths_supplier,
    media_root: str | Path | None,
    metadata_root: str | Path | None,
    must_contain: str | None = None,
) -> GroupIndex:
    index = _INDEX_CACHE.get(cache_key)
    if index is None or (must_contain is not None and not index.contains(must_contain)):
        index = build_group_index(paths_supplier(), media_root, metadata_root)
        _INDEX_CACHE[cache_key] = index
    return index


def reset_group_index_cache() -> None:
    _INDEX_CACHE.clear()
