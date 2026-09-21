"""Where the controllers left each movable screen, and the reach a drag is held to."""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import fields, replace
from pathlib import Path

from .scene import Placement, turn_deg

logger = logging.getLogger(__name__)

_FIELDS = tuple(field.name for field in fields(Placement))

MAIN = "main"
PORTRAIT = "portrait"
LANDSCAPE = "landscape"
PANEL = "panel"
DASH = "dash"
REFERENCE = "reference"
LIBRARY = "library"
LAYOUT_FILENAME = "vr_layout.json"

PLAYERS = (MAIN, LANDSCAPE, PORTRAIT)


AZIMUTH_LIMIT_DEG = 150.0
ELEVATION_LIMIT_DEG = 75.0
MIN_WIDTH_DEG = 10.0
MAX_WIDTH_DEG = 120.0


def clamp_width(width_deg: float) -> float:
    return max(MIN_WIDTH_DEG, min(MAX_WIDTH_DEG, width_deg))


def clamp_elevation(elevation_deg: float) -> float:
    return max(-ELEVATION_LIMIT_DEG, min(ELEVATION_LIMIT_DEG, elevation_deg))


def clamp_placement(placement: Placement) -> Placement:
    return Placement(
        azimuth_deg=max(-AZIMUTH_LIMIT_DEG, min(AZIMUTH_LIMIT_DEG, placement.azimuth_deg)),
        elevation_deg=clamp_elevation(placement.elevation_deg),
        width_deg=clamp_width(placement.width_deg),
    )


def grown(placement: Placement, factor: float) -> Placement:
    return replace(placement, width_deg=clamp_width(placement.width_deg * factor))


def _held_to_the_edge(factor: float, center_deg: float, offset_deg: float,
                      limit_deg: float) -> float:
    if not offset_deg:
        return factor
    edge = limit_deg if offset_deg > 0 else -limit_deg
    return min(factor, (edge - center_deg) / offset_deg)


def _held_within_the_scene(factor: float, placements: Mapping[str, Placement],
                           about: Placement) -> float:
    if factor < 1.0:
        return max([factor, *(MIN_WIDTH_DEG / placement.width_deg
                              for placement in placements.values())])
    for placement in placements.values():
        factor = _held_to_the_edge(
            factor, about.azimuth_deg, turn_deg(about.azimuth_deg, placement.azimuth_deg),
            AZIMUTH_LIMIT_DEG)
        factor = _held_to_the_edge(
            factor, about.elevation_deg, placement.elevation_deg - about.elevation_deg,
            ELEVATION_LIMIT_DEG)
        factor = min(factor, MAX_WIDTH_DEG / placement.width_deg)
    return factor


def nearer(placements: Mapping[str, Placement], factor: float, *,
           about: Placement) -> dict[str, Placement]:
    factor = _held_within_the_scene(factor, placements, about)
    return {
        name: Placement(
            azimuth_deg=about.azimuth_deg + factor * turn_deg(
                about.azimuth_deg, placement.azimuth_deg),
            elevation_deg=about.elevation_deg + factor * (
                placement.elevation_deg - about.elevation_deg),
            width_deg=placement.width_deg * factor,
        )
        for name, placement in placements.items()
    }


def rearranged(placements: Mapping[str, Placement], *, grow: float,
               nearer_by: float) -> dict[str, Placement]:
    moved = dict(placements)
    if grow != 1.0:
        moved[MAIN] = grown(moved[MAIN], grow)
    if nearer_by != 1.0:
        moved = nearer(moved, nearer_by, about=moved[MAIN])
    return {name: placement for name, placement in moved.items()
            if placement != placements[name]}


# The word this file knew the main screen by before "main".
_LAST_SESSIONS_MAIN = "primary"


def migrate_layout(path: Path) -> bool:
    """Rewrite *path* once if it still names the main screen by its old word:
    a screen :func:`read_layout` finds nothing for starts in its own default
    spot, which would have dropped where he placed the main screen."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict) or _LAST_SESSIONS_MAIN not in raw:
        return False
    raw.setdefault(MAIN, raw.pop(_LAST_SESSIONS_MAIN))
    raw.pop(_LAST_SESSIONS_MAIN, None)
    try:
        Path(path).write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.warning("Could not rewrite the layout in %s", path, exc_info=True)
        return False
    return True


def read_layout(path: Path) -> dict[str, Placement]:
    """Only where the last session was left holding each screen."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    remembered: dict[str, Placement] = {}
    for name, spot in raw.items():
        if not isinstance(spot, dict):
            continue
        try:
            remembered[name] = clamp_placement(
                Placement(**{field: float(spot[field]) for field in _FIELDS}))
        except (KeyError, TypeError, ValueError):
            logger.warning("Ignoring the remembered %s placement in %s", name, path)
    return remembered


def write_layout(path: Path, layout: dict[str, Placement]) -> bool:
    payload = {
        name: {field: getattr(placement, field) for field in _FIELDS}
        for name, placement in layout.items()
    }
    try:
        Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.warning("Could not remember the layout in %s", path, exc_info=True)
        return False
    return True
