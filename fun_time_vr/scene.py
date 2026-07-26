"""Where each player's picture hangs in the VR scene — pure geometry.

One forward band of view: the primary player spans the middle, and each
satellite floats beside it, portrait on the left and landscape on the right.
Every screen is a gently curved patch of one cylinder around the viewer (a
flat 2D video reads better with a slight wrap at this scale), built here as
triangle-strip vertices for the renderer to draw.  Immersive projections
(equirect/fisheye) don't use these patches at all — they fill the view from a
shader — so this module is the whole of the "windowed" layout.

The satellites draw after (so over) the primary, which is what keeps them
visible when a VR video wraps the entire hemisphere behind them — and is also
why they may overlap the primary's edges.
"""
from __future__ import annotations

import math

import numpy as np

# One cylinder for every screen.  The view matrix is rotation-only (no head
# translation reaches the scene), so the radius sets apparent scale only.
RADIUS = 2.0

PRIMARY_WIDTH_DEG = 72.0

# Tuned on the first headset run: satellites flush beside the primary
# (36° wide, centers at ±54°) landed in the peripheral vision on a wide-FOV
# headset — the user had to turn to see either one.  So they shrink a little,
# tuck inward over the primary's edges, and ride slightly above center.
SATELLITE_WIDTH_DEG = 28.0
SATELLITE_AZIMUTH_DEG = 38.0
SATELLITE_ELEVATION_DEG = 10.0

_SATELLITE_AZIMUTH_BY_SIDE = {
    "portrait": -SATELLITE_AZIMUTH_DEG,
    "landscape": SATELLITE_AZIMUTH_DEG,
}

# Enough columns that the curve reads smooth; the patch is cheap either way.
CURVE_SEGMENTS = 16


def satellite_center_azimuth(side: str) -> float:
    """Degrees off straight-ahead for a satellite's screen center (left < 0)."""
    return _SATELLITE_AZIMUTH_BY_SIDE[side]


def surface_vertices(
    center_azimuth_deg: float,
    width_deg: float,
    *,
    aspect: float,
    center_elevation_deg: float = 0.0,
    radius: float = RADIUS,
    segments: int = CURVE_SEGMENTS,
) -> np.ndarray:
    """Triangle-strip vertices for one curved screen: (x, y, z, u, v) rows.

    The screen subtends *width_deg* of the cylinder centered on
    *center_azimuth_deg* (degrees right of forward); its height is the arc
    length over *aspect* (pixel width/height), so the video fills it edge to
    edge without letterboxing, and its center rides at *center_elevation_deg*
    above the horizon.  Columns run left to right, two vertices each (top v=1,
    then bottom v=0), ready for GL_TRIANGLE_STRIP.
    """
    if aspect <= 0:
        raise ValueError(f"aspect must be positive, got {aspect}")
    width_rad = math.radians(width_deg)
    half_height = (radius * width_rad / aspect) / 2
    lift = radius * math.tan(math.radians(center_elevation_deg))
    start = math.radians(center_azimuth_deg) - width_rad / 2

    rows: list[tuple[float, float, float, float, float]] = []
    for column in range(segments + 1):
        t = column / segments
        azimuth = start + t * width_rad
        x = radius * math.sin(azimuth)
        z = -radius * math.cos(azimuth)
        rows.append((x, lift + half_height, z, t, 1.0))
        rows.append((x, lift - half_height, z, t, 0.0))
    return np.array(rows, dtype=np.float32)
