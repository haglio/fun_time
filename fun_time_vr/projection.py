"""Which shape a video is watched on, and how FunTimeVR remembers the answer.

A 2D video hangs on a big gently-curved screen; a VR video wraps the view in
one of the projections its producer mastered it in (equirect 180 side-by-side
is the overwhelming default, fisheye variants the exceptions).  The user fixes
a wrong guess once — cycling with the P key or the spoken "projection" — and
the choice is written into the video's Evolver metadata sidecar under a
``"vr"`` block of its own, so it holds for good.  Writes are read-merge-write,
the same discipline Evolver's own writers use, so the two sides never clobber
each other's fields.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Sequence

from fun_time.media_metadata import load_metadata, metadata_path_for

logger = logging.getLogger(__name__)

FLAT = "flat"
EQUIRECT_180_SBS = "equirect_180_sbs"
FISHEYE_190_SBS = "fisheye_190_sbs"
MKX200_SBS = "mkx200_sbs"
EQUIRECT_360 = "equirect_360"

# The cycle order: the P key / "projection" walks this ring.  Flat first, so a
# mis-detected 2D video is one press away from every VR video's landing spot.
PROJECTIONS: tuple[str, ...] = (
    FLAT,
    EQUIRECT_180_SBS,
    FISHEYE_190_SBS,
    MKX200_SBS,
    EQUIRECT_360,
)

# Filename tokens that name a projection outright, checked lowercased and in
# order (a fisheye master often carries "180" too, so fisheye must win).
_FILENAME_HINTS: tuple[tuple[str, str], ...] = (
    ("mkx200", MKX200_SBS),
    ("fisheye", FISHEYE_190_SBS),
    ("rf52", FISHEYE_190_SBS),
    ("_360", EQUIRECT_360),
    ("_180", EQUIRECT_180_SBS),
    ("180_", EQUIRECT_180_SBS),
)

_SIDECAR_BLOCK = "vr"
_PROJECTION_FIELD = "projection"


def is_vr_video(video_path: str | Path, vr_dirs: Sequence[Path | str]) -> bool:
    """Whether a video lives in one of the configured VR library dirs."""
    path = Path(video_path)
    for root in vr_dirs:
        try:
            path.relative_to(Path(root))
        except ValueError:
            continue
        return True
    return False


def default_projection(video_path: str, vr_dirs: Sequence[Path | str]) -> str:
    """The projection a video opens in before anyone has chosen one.

    A filename that names its own projection is believed wherever the file
    lives; otherwise anything under a configured VR library dir is the library
    convention (180 SBS equirect), and everything else is an ordinary flat
    video.
    """
    name = Path(video_path).name.lower()
    for token, projection in _FILENAME_HINTS:
        if token in name:
            return projection
    if is_vr_video(video_path, vr_dirs):
        return EQUIRECT_180_SBS
    return FLAT


def next_projection(current: str) -> str:
    """The next stop on the cycle, restarting it from a retired/unknown value."""
    try:
        position = PROJECTIONS.index(current)
    except ValueError:
        return PROJECTIONS[0]
    return PROJECTIONS[(position + 1) % len(PROJECTIONS)]


def saved_projection(video_path: str, metadata_root: Path | None) -> str | None:
    """The projection remembered in the video's sidecar, or None.

    A value the cycle no longer contains reads as unset rather than surviving
    as an unrenderable mode.
    """
    sidecar = metadata_path_for(video_path, metadata_root)
    if sidecar is None or not sidecar.is_file():
        return None
    block = load_metadata(sidecar).get(_SIDECAR_BLOCK)
    value = block.get(_PROJECTION_FIELD) if isinstance(block, dict) else None
    return value if value in PROJECTIONS else None


def save_projection(video_path: str, metadata_root: Path | None, projection: str) -> bool:
    """Remember *projection* in the video's sidecar; False when it has none.

    Read-merge-write with Evolver's own file shape (indent=2, trailing
    newline), touching only the ``vr`` block, so every field another writer
    owns rides through untouched.  A video outside the mirrored library has no
    sidecar path, and gets no stray file invented for it.
    """
    sidecar = metadata_path_for(video_path, metadata_root)
    if sidecar is None:
        logger.info("No sidecar path for %s; projection not remembered", video_path)
        return False
    payload = load_metadata(sidecar) if sidecar.is_file() else {}
    payload.setdefault(_SIDECAR_BLOCK, {})[_PROJECTION_FIELD] = projection
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.warning("Could not write sidecar %s", sidecar, exc_info=True)
        return False
    return True


def resolve_projection(
    video_path: str, metadata_root: Path | None, vr_dirs: Sequence[Path | str]
) -> str:
    """What to open *video_path* in: the remembered choice, else the default."""
    return saved_projection(video_path, metadata_root) or default_projection(video_path, vr_dirs)
