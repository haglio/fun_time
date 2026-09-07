"""Where each movable screen hangs by default, and where the controllers left it."""
from __future__ import annotations

import json
import logging
from dataclasses import fields
from pathlib import Path

from .scene import PRIMARY_PLACEMENT, Placement

logger = logging.getLogger(__name__)

_FIELDS = tuple(field.name for field in fields(Placement))

PRIMARY = "primary"
PORTRAIT = "portrait"
LANDSCAPE = "landscape"
PANEL = "panel"
DASH = "dash"
LAYOUT_FILENAME = "vr_layout.json"

# Sides as on the desktop: landscape left of the main player, portrait right.  Tuned on
# the first headset run — satellites flush beside the primary sat in the peripheral
# vision, so they tuck inward over its edges and ride a little high.
DEFAULT_LAYOUT: dict[str, Placement] = {
    PRIMARY: PRIMARY_PLACEMENT,
    LANDSCAPE: Placement(azimuth_deg=-38.0, elevation_deg=10.0, width_deg=28.0),
    PORTRAIT: Placement(azimuth_deg=38.0, elevation_deg=10.0, width_deg=28.0),
    # Above the main player and clear of the console: a wide panel anywhere
    # lower covers a satellite.
    DASH: Placement(azimuth_deg=0.0, elevation_deg=52.0, width_deg=40.0),
}

AZIMUTH_LIMIT_DEG = 150.0
ELEVATION_LIMIT_DEG = 75.0
MIN_WIDTH_DEG = 10.0
MAX_WIDTH_DEG = 360.0  # a full turn: past it screen_uv cannot tell the edges apart


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
