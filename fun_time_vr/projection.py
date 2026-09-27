"""Which shape a video is watched on, and how FunTimeVR remembers the answer.

A 2D video hangs on a big flat screen; a VR video wraps the view in one of the
projections its producer mastered it in (equirect 180 side-by-side the default,
fisheye variants the exceptions).  A wrong guess is fixed once -- the P key, the
spoken "projection", a controller's stick -- and the choice is written into the
video's Evolver sidecar under a ``"vr"`` block of its own, read-merge-write, so
neither side clobbers the other's fields.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fun_time.media_metadata import load_metadata, metadata_path_for
from fun_time.vr_videos import VR_FILENAME_TOKENS, is_vr_video

from .picture_shape import FISHEYE_CIRCLE

logger = logging.getLogger(__name__)

FLAT = "flat"
EQUIRECT_180_SBS = "equirect_180_sbs"
FISHEYE_180_SBS = "fisheye_180_sbs"
FISHEYE_190_SBS = "fisheye_190_sbs"
MKX200_SBS = "mkx200_sbs"
FISHEYE_220_SBS = "fisheye_220_sbs"
FISHEYE_200_STEREOGRAPHIC_SBS = "fisheye_200_stereographic_sbs"
FISHEYE_200_EQUISOLID_SBS = "fisheye_200_equisolid_sbs"
EQUIRECT_360 = "equirect_360"

# The cycle order: the P key / "projection" walks this ring.  Flat first, so a
# mis-detected 2D video is one press away from every VR video's landing spot;
# see render.Wrap.fisheye_curve for why the last two share MKX200's own angle.
PROJECTIONS: tuple[str, ...] = (
    FLAT,
    EQUIRECT_180_SBS,
    FISHEYE_180_SBS,
    FISHEYE_190_SBS,
    MKX200_SBS,
    FISHEYE_220_SBS,
    FISHEYE_200_STEREOGRAPHIC_SBS,
    FISHEYE_200_EQUISOLID_SBS,
    EQUIRECT_360,
)

# Which projection each of the tokens that name one stands for, read in the
# order they are listed there.  The tokens themselves live beside the predicate
# that only asks whether a name carries one, so a mastering cannot be known to
# the filter and unknown to the renderer.
_HINT_PROJECTIONS: dict[str, str] = {
    "mkx200": MKX200_SBS,
    "fisheye": FISHEYE_190_SBS,
    "rf52": FISHEYE_190_SBS,
    "_360": EQUIRECT_360,
    "_180": EQUIRECT_180_SBS,
    "180_": EQUIRECT_180_SBS,
}
_FILENAME_HINTS: tuple[tuple[str, str], ...] = tuple(
    (token, _HINT_PROJECTIONS[token]) for token in VR_FILENAME_TOKENS
)

_SIDECAR_BLOCK = "vr"
_PROJECTION_FIELD = "projection"
_PICTURE_FIELD = "picture"


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


def _stepped(current: str, step: int) -> str:
    try:
        position = PROJECTIONS.index(current)
    except ValueError:
        return PROJECTIONS[0]  # a retired or unknown value restarts the ring
    return PROJECTIONS[(position + step) % len(PROJECTIONS)]


def next_projection(current: str) -> str:
    return _stepped(current, 1)


def previous_projection(current: str) -> str:
    return _stepped(current, -1)


@dataclass(frozen=True)
class ProjectionMemory:
    metadata_root: Path | None
    vr_dirs: tuple[Path | str, ...] = ()

    def resolve(self, video_path: str) -> str:
        chosen = self.saved(video_path)
        if chosen:
            return chosen
        named = default_projection(video_path, self.vr_dirs)
        if named == EQUIRECT_180_SBS and self._kept(video_path, _PICTURE_FIELD) == FISHEYE_CIRCLE:
            return FISHEYE_180_SBS
        return named

    def saved(self, video_path: str) -> str | None:
        value = self._kept(video_path, _PROJECTION_FIELD)
        return value if value in PROJECTIONS else None  # a retired one reads as unset

    def save(self, video_path: str, projection: str) -> bool:
        return self._keep(video_path, _PROJECTION_FIELD, projection)

    def note_shape(self, video_path: str, shape: str) -> bool:
        return self._keep(video_path, _PICTURE_FIELD, shape)

    def wants_a_look(self, video_path: str) -> bool:
        return (default_projection(video_path, self.vr_dirs) == EQUIRECT_180_SBS
                and not self.saved(video_path)
                and not self._kept(video_path, _PICTURE_FIELD))

    def _kept(self, video_path: str, field: str) -> str | None:
        sidecar = self._sidecar(video_path)
        if sidecar is None or not sidecar.is_file():
            return None
        block = load_metadata(sidecar).get(_SIDECAR_BLOCK)
        return block.get(field) if isinstance(block, dict) else None

    def _keep(self, video_path: str, field: str, value: str) -> bool:
        sidecar = self._sidecar(video_path)
        if sidecar is None:
            logger.info("No sidecar path for %s; its %s is not remembered", video_path, field)
            return False
        payload = load_metadata(sidecar) if sidecar.is_file() else {}
        payload.setdefault(_SIDECAR_BLOCK, {})[field] = value
        try:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        except OSError:
            logger.warning("Could not write sidecar %s", sidecar, exc_info=True)
            return False
        return True

    def _sidecar(self, video_path: str) -> Path | None:
        return metadata_path_for(video_path, self.metadata_root, outlying_dirs=self.vr_dirs)
