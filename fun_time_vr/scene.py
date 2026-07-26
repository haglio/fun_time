"""Where each player's picture hangs in the VR scene — pure geometry.

One forward band of view, split the way the user specified: the primary player
gets the middle half, and each satellite floats beside it with about a quarter,
portrait on the left and landscape on the right.  Every screen is a gently
curved patch of one cylinder around the viewer (a flat 2D video reads better
with a slight wrap at this scale), built here as triangle-strip vertices for
the renderer to draw.  Immersive projections (equirect/fisheye) don't use these
patches at all — they fill the view from a shader — so this module is the whole
of the "windowed" layout.

The satellites draw after (so over) the primary, which is what keeps them
visible when a VR video wraps the entire hemisphere behind them.
"""
from __future__ import annotations

import math

import numpy as np

# One cylinder for every screen.  The view matrix is rotation-only (no head
# translation reaches the scene), so the radius sets apparent scale only.
RADIUS = 2.0

# The forward band: primary spans the middle half, a satellite a quarter each.
PRIMARY_WIDTH_DEG = 72.0
SATELLITE_WIDTH_DEG = 36.0

_SATELLITE_AZIMUTH_DEG = {
    "portrait": -(PRIMARY_WIDTH_DEG + SATELLITE_WIDTH_DEG) / 2,
    "landscape": (PRIMARY_WIDTH_DEG + SATELLITE_WIDTH_DEG) / 2,
}

# Enough columns that the curve reads smooth; the patch is cheap either way.
CURVE_SEGMENTS = 16


def satellite_center_azimuth(side: str) -> float:
    """Degrees off straight-ahead for a satellite's screen center (left < 0)."""
    return _SATELLITE_AZIMUTH_DEG[side]


def surface_vertices(
    center_azimuth_deg: float,
    width_deg: float,
    *,
    aspect: float,
    radius: float = RADIUS,
    segments: int = CURVE_SEGMENTS,
) -> np.ndarray:
    """Triangle-strip vertices for one curved screen: (x, y, z, u, v) rows.

    The screen subtends *width_deg* of the cylinder centered on
    *center_azimuth_deg* (degrees right of forward); its height is the arc
    length over *aspect* (pixel width/height), so the video fills it edge to
    edge without letterboxing.  Columns run left to right, two vertices each
    (top v=1, then bottom v=0), ready for GL_TRIANGLE_STRIP.
    """
    if aspect <= 0:
        raise ValueError(f"aspect must be positive, got {aspect}")
    width_rad = math.radians(width_deg)
    half_height = (radius * width_rad / aspect) / 2
    start = math.radians(center_azimuth_deg) - width_rad / 2

    rows: list[tuple[float, float, float, float, float]] = []
    for column in range(segments + 1):
        t = column / segments
        azimuth = start + t * width_rad
        x = radius * math.sin(azimuth)
        z = -radius * math.cos(azimuth)
        rows.append((x, half_height, z, t, 1.0))
        rows.append((x, -half_height, z, t, 0.0))
    return np.array(rows, dtype=np.float32)
