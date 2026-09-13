"""Where each movable screen hangs by default, and where the controllers left it."""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import fields, replace
from pathlib import Path

from .scene import PRIMARY_PLACEMENT, Placement, turn_deg

logger = logging.getLogger(__name__)

_FIELDS = tuple(field.name for field in fields(Placement))

PRIMARY = "primary"
PORTRAIT = "portrait"
LANDSCAPE = "landscape"
PANEL = "panel"
DASH = "dash"
REFERENCE = "reference"
LAYOUT_FILENAME = "vr_layout.json"

# Sides as on the desktop: landscape left of the main player, portrait right.  Tuned on
# the first headset run — satellites flush beside the primary sat in the peripheral
# vision, so they tuck inward over its edges and ride a little high.
DEFAULT_LAYOUT: dict[str, Placement] = {
    PRIMARY: PRIMARY_PLACEMENT,
    LANDSCAPE: Placement(azimuth_deg=-38.0, elevation_deg=10.0, width_deg=28.0),
    PORTRAIT: Placement(azimuth_deg=38.0, elevation_deg=10.0, width_deg=28.0),
    # The dashboard with the console under it, the video having wrapped the viewer:
    PANEL: Placement(azimuth_deg=0.0, elevation_deg=-11.0, width_deg=40.0),
    # Above the main player and clear of the console: a wide panel anywhere
    # lower covers a satellite.
    DASH: Placement(azimuth_deg=0.0, elevation_deg=52.0, width_deg=40.0),
    # A table to read: dead ahead and wide, over the picture while it is up.
    REFERENCE: Placement(azimuth_deg=0.0, elevation_deg=6.0, width_deg=54.0),
}


def default_player_layout() -> dict[str, Placement]:
    return {name: DEFAULT_LAYOUT[name] for name in (PRIMARY, LANDSCAPE, PORTRAIT)}


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
        moved[PRIMARY] = grown(moved[PRIMARY], grow)
    if nearer_by != 1.0:
        moved = nearer(moved, nearer_by, about=moved[PRIMARY])
    return {name: placement for name, placement in moved.items()
            if placement != placements[name]}


def read_layout(path: Path) -> dict[str, Placement]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    layout = dict(DEFAULT_LAYOUT)
    for name in layout:
        remembered = raw.get(name) if isinstance(raw, dict) else None
        if not isinstance(remembered, dict):
            continue
        try:
            layout[name] = clamp_placement(
                Placement(**{field: float(remembered[field]) for field in _FIELDS}))
        except (KeyError, TypeError, ValueError):
            logger.warning("Ignoring the remembered %s placement in %s", name, path)
    return layout


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
