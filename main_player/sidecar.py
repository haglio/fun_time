"""Reading the metadata sidecar Evolver keeps beside every library video.

The metadata tree mirrors the video library one-to-one (``…/videos/metadata``
pairs with ``…/videos/videos``), so a video's record sits at the same path under
the metadata root as the video sits under the library root. Evolver owns what
goes in; the main player only reads, and a missing or malformed file is the ordinary case
rather than an error — plenty of videos predate the metadata.
"""
from __future__ import annotations

import json
from pathlib import Path

from .video_kind import EXCERPT


def sidecar_for(video: Path, metadata_root: Path) -> Path | None:
    """Where Evolver's metadata for *video* lives, or None if *video* is outside.

    Two roots, because Genau's delivered loops sit *beside* the video tree
    (``videos/genau/clips``) rather than inside it, and Evolver files their
    records the same way it files the library's: mirrored from the folder that
    holds both, so the loop's lands at ``metadata/genau/clips``.
    """
    library = metadata_root.parent / "videos"
    for root in (library, metadata_root.parent):
        try:
            rel = video.relative_to(root)
        except ValueError:
            continue
        return (metadata_root / rel).with_suffix(".json")
    return None


def read_sidecar(video: Path, metadata_root: Path) -> dict:
    """Everything Evolver recorded for *video*; empty when there is nothing."""
    sidecar = sidecar_for(video, metadata_root)
    if sidecar is None:
        return {}
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_clip(video: Path, metadata_root: Path) -> dict | None:
    """The ``clip`` object Evolver recorded for *video*, or None.

    Present only on a scene carved out of a compilation: it names the parent
    compilation, the running order within it, and the source movie + performer.
    """
    clip = read_sidecar(video, metadata_root).get("clip")
    return clip if isinstance(clip, dict) else None


def read_version_group(video: Path, metadata_root: Path) -> str | None:
    """The version-family id Evolver recorded for *video*, or None.

    Every re-encode, upscale and hand-renamed variant of one video carries the
    same id, which is what makes it the authority on "same video, other version"
    — the names alone cannot say so (see Evolver's ``NONAI_VERSION_OVERRIDES``).
    None means no record, so the caller falls back to reading the names.
    """
    version = read_sidecar(video, metadata_root).get("version")
    if not isinstance(version, dict):
        return None
    group = version.get("group")
    return str(group) if group else None


def read_video_type(video: Path, metadata_root: Path) -> str:
    """The kind Evolver recorded for *video*, or ``""`` when it recorded none.

    One field standing in for the several tests this player used to run — a
    running time against a threshold of its own, a folder the loops are
    delivered to, the presence of a ``clip`` record.  Evolver settles all of
    that once, for the whole library; empty means a video it has not reached
    yet, and the caller falls back to measuring.

    The one older record still read here is the ``clip`` object: it says a scene
    was carved out of a longer one, which is exactly what the kind now says, and
    it was on these sidecars before there was a kind to write.  So a library
    Evolver has not been over since keeps its carved scenes out of full-length
    rather than waiting for the run that records them.
    """
    payload = read_sidecar(video, metadata_root)
    video_block = payload.get("video")
    if isinstance(video_block, dict) and video_block.get("type"):
        return str(video_block["type"])
    return EXCERPT if isinstance(payload.get("clip"), dict) else ""
