"""Where each movable screen hangs by default, and where the controllers left it."""
from __future__ import annotations

import json
import logging
from dataclasses import fields
from pathlib import Path

from .scene import Placement

logger = logging.getLogger(__name__)

_FIELDS = tuple(field.name for field in fields(Placement))

PORTRAIT = "portrait"
LANDSCAPE = "landscape"
PANEL = "panel"
LAYOUT_FILENAME = "vr_layout.json"

# Tuned on the first headset run: satellites flush beside the primary (36° wide,
# centers at ±54°) sat in the peripheral vision, so they tuck inward over its
# edges and ride a little high; the panel hangs above its top edge, out of the
# action, which sits low in an immersive picture.
DEFAULT_LAYOUT: dict[str, Placement] = {
    PORTRAIT: Placement(azimuth_deg=-38.0, elevation_deg=10.0, width_deg=28.0),
    LANDSCAPE: Placement(azimuth_deg=38.0, elevation_deg=10.0, width_deg=28.0),
    PANEL: Placement(azimuth_deg=0.0, elevation_deg=32.0, width_deg=24.0),
}

AZIMUTH_LIMIT_DEG = 150.0
ELEVATION_LIMIT_DEG = 75.0
MIN_WIDTH_DEG = 10.0
MAX_WIDTH_DEG = 90.0


def clamp_placement(placement: Placement) -> Placement:
    return Placement(
        azimuth_deg=max(-AZIMUTH_LIMIT_DEG, min(AZIMUTH_LIMIT_DEG, placement.azimuth_deg)),
        elevation_deg=max(-ELEVATION_LIMIT_DEG, min(ELEVATION_LIMIT_DEG, placement.elevation_deg)),
        width_deg=max(MIN_WIDTH_DEG, min(MAX_WIDTH_DEG, placement.width_deg)),
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
