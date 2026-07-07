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


def search_haystack(metadata: dict) -> str:
    """Lowercased, whitespace-collapsed text a filter query is matched against.

    Combines the structured video *action* with the positive prompts (the video
    prompt and the source image's positive prompt).  The negative prompt is
    deliberately excluded — a clip that says "no delta" must not match a
    query for "delta".
    """
    video = metadata.get("video") or {}
    source = metadata.get("source_image") or {}
    parts = (video.get("action"), video.get("prompt"), source.get("positive_prompt"))
    return _norm_text(" ".join(str(part) for part in parts if part))


def matches_query(metadata: dict, query: str) -> bool:
    """Whether *metadata* satisfies *query* — an empty query matches everything.

    A query matches when it appears as a contiguous substring of the video's
    search haystack, so "alpha" catches the "Alpha, Theta Motion" action and
    "beta gamma" catches the "Beta Gamma" action while ignoring word order noise.
    """
    normalized = _norm_text(query)
    if not normalized:
        return True
    return normalized in search_haystack(metadata)


def path_matches_query(
    video_path: str,
    media_root: str | Path | None,
    metadata_root: str | Path | None,
    query: str,
) -> bool:
    """Whether the sidecar for *video_path* satisfies *query*.

    An empty query passes every video.  A non-empty query can only be satisfied
    by a video that has a metadata sidecar, so videos without one drop out of a
    filtered build.
    """
    if not _norm_text(query):
        return True
    sidecar = metadata_path_for(video_path, media_root, metadata_root)
    if sidecar is None or not sidecar.is_file():
        return False
    return matches_query(load_metadata(sidecar), query)


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

# The loose family keeps only the scene's semantic identity — the prompts, and
# how the subject is cast (image ``style`` / video ``action``) — while freeing
# the render knobs (model, resolution, aspect ratio, quality, creativity) along
# with the seed.  It backs "widen the net": when no exact same-config sister
# exists, a clip that differs only in a render setting is still very-nearly it.
_IMAGE_LOOSE_FAMILY_FIELDS = ("positive_prompt", "negative_prompt", "style")
_VIDEO_LOOSE_FAMILY_FIELDS = ("prompt", "action")


def _seed_key(
    metadata: dict, image_fields: tuple[str, ...], video_fields: tuple[str, ...]
) -> tuple[str, str] | None:
    """(family, seed) for a video, familied by *image_fields* / *video_fields*.

    Returns None when the metadata lacks a seed or a prompt to family by.
    """
    source = metadata.get("source_image")
    if source:
        if not source.get("seed") or not source.get("positive_prompt"):
            return None
        return _field_key("img", source, image_fields), _norm_text(source.get("seed"))
    video = metadata.get("video") or {}
    if not video.get("seed") or not video.get("prompt"):
        return None
    return _field_key("t2v", video, video_fields), _norm_text(video.get("seed"))


def seed_group_key(metadata: dict) -> tuple[str, str] | None:
    """(family, seed) placing a video among its same-config-different-seed kin.

    Videos sharing a family were generated from the identical configuration
    with only the seed varied — the same scenario cast with a different subject.
    """
    return _seed_key(metadata, _IMAGE_FAMILY_FIELDS, _VIDEO_BASE_FIELDS + ("action",))


def loose_seed_group_key(metadata: dict) -> tuple[str, str] | None:
    """(family, seed) placing a video among its same-scene kin, render knobs freed.

    Wider than :func:`seed_group_key`: only the prompts and the cast/action are
    held fixed, so configs differing solely in a render setting still family
    together.  Used as the fallback when no exact seed sister exists.
    """
    return _seed_key(metadata, _IMAGE_LOOSE_FAMILY_FIELDS, _VIDEO_LOOSE_FAMILY_FIELDS)


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
    loose_seed_key_by_path: dict[str, tuple[str, str]]
    loose_seed_members: dict[str, list[str]]
    indexed_paths: frozenset[str]

    def contains(self, path: str) -> bool:
        return normalize_path_key(path) in self.indexed_paths


def _record_seed_membership(
    key: tuple[str, str] | None,
    path: str,
    key_by_path: dict[str, tuple[str, str]],
    members: dict[str, list[str]],
) -> None:
    """File *path* under its ``(family, seed)`` *key*, if it has one."""
    if key is None:
        return
    key_by_path[normalize_path_key(path)] = key
    members.setdefault(key[0], []).append(path)


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
    loose_seed_key_by_path: dict[str, tuple[str, str]] = {}
    loose_seed_members: dict[str, list[str]] = {}
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
        _record_seed_membership(seed_group_key(metadata), path, seed_key_by_path, seed_members)
        _record_seed_membership(
            loose_seed_group_key(metadata), path, loose_seed_key_by_path, loose_seed_members
        )
    for members in (*action_members.values(), *seed_members.values(), *loose_seed_members.values()):
        members.sort()
    return GroupIndex(
        action_key_by_path=action_key_by_path,
        action_members=action_members,
        seed_key_by_path=seed_key_by_path,
        seed_members=seed_members,
        loose_seed_key_by_path=loose_seed_key_by_path,
        loose_seed_members=loose_seed_members,
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
