"""The geometry of one screen in the VR scene: where a picture hangs, and the
curved patch that carries it.

Every screen is a gently curved patch of one cylinder around the viewer (a
flat 2D video reads better with a slight wrap at this scale), built here as
triangle-strip vertices for the renderer to draw.  Immersive projections
(equirect/fisheye) don't use these patches at all — they fill the view from a
shader — so this module is the whole of the "windowed" layout.

The satellites draw after (so over) the primary, which is what keeps them
visible when a VR video wraps the entire hemisphere at their back — and is also
why they may overlap the main player's edges.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# One cylinder for every screen.  The view matrix is rotation-only (no head
# translation reaches the scene), so the radius sets apparent scale only.
RADIUS = 2.0

PRIMARY_WIDTH_DEG = 72.0

# Enough columns that the curve reads smooth; the patch is cheap either way.
CURVE_SEGMENTS = 16


@dataclass(frozen=True)
class Placement:
    azimuth_deg: float
    elevation_deg: float
    width_deg: float


PRIMARY_PLACEMENT = Placement(0.0, 0.0, PRIMARY_WIDTH_DEG)


def _quat_multiply(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def _quat_rotate(
    q: tuple[float, float, float, float], v: tuple[float, float, float]
) -> tuple[float, float, float]:
    x, y, z, w = q
    vx, vy, vz = v
    return (
        (1 - 2 * (y * y + z * z)) * vx + 2 * (x * y - w * z) * vy + 2 * (x * z + w * y) * vz,
        2 * (x * y + w * z) * vx + (1 - 2 * (x * x + z * z)) * vy + 2 * (y * z - w * x) * vz,
        2 * (x * z - w * y) * vx + 2 * (y * z + w * x) * vy + (1 - 2 * (x * x + y * y)) * vz,
    )


def scene_placement_quaternion(
    yaw_deg: float, pitch_deg: float
) -> tuple[float, float, float, float]:
    half_yaw = math.radians(yaw_deg) / 2.0
    half_pitch = math.radians(pitch_deg) / 2.0
    yaw = (0.0, math.sin(half_yaw), 0.0, math.cos(half_yaw))
    pitch = (math.sin(half_pitch), 0.0, 0.0, math.cos(half_pitch))
    return _quat_multiply(yaw, pitch)


def quad_layer_placement(
    placement: Placement,
    *,
    aspect: float,
    scene_yaw_deg: float = 0.0,
    scene_pitch_deg: float = 0.0,
    radius: float = RADIUS,
) -> tuple[tuple[float, float, float], tuple[float, float, float, float], tuple[float, float]]:
    """Pose and size for the flat compositor quad standing in for a screen.

    When the runtime composites a screen as an ``XrCompositionLayerQuad``, the
    gently-curved patch flattens to its tangent plane: same center point on
    the cylinder, yaw-only orientation facing the viewer (matching the
    untilted columns of :func:`surface_vertices`), and a width chosen so the
    flat quad subtends exactly the placement's width from the origin — the
    sagitta of a curve this gentle is centimeters, so the swap reads identical
    in the headset.  Returns ``(position, orientation_xyzw, (width, height))``
    in the reference space's meters, height from *aspect* as ever.

    """
    if aspect <= 0:
        raise ValueError(f"aspect must be positive, got {aspect}")
    theta = math.radians(placement.azimuth_deg)
    position = (
        radius * math.sin(theta),
        radius * math.tan(math.radians(placement.elevation_deg)),
        -radius * math.cos(theta),
    )
    # A rotation about +Y by -theta points the quad's +Z (its front face,
    # per the OpenXR quad-layer convention) back at the viewer.
    orientation = (0.0, math.sin(-theta / 2.0), 0.0, math.cos(theta / 2.0))
    scene = scene_placement_quaternion(scene_yaw_deg, scene_pitch_deg)
    position = _quat_rotate(scene, position)
    orientation = _quat_multiply(scene, orientation)
    width = 2.0 * radius * math.tan(math.radians(placement.width_deg) / 2.0)
    return position, orientation, (width, width / aspect)


def attached_below(
    placement: Placement,
    *,
    aspect: float,
    width_deg: float,
    hanging_aspect: float,
    gap_deg: float = 0.0,
    radius: float = RADIUS,
) -> Placement:
    lower = (radius * math.tan(math.radians(placement.elevation_deg))
             - radius * math.radians(placement.width_deg) / aspect / 2)
    half_height = radius * math.radians(width_deg) / hanging_aspect / 2
    center = lower - radius * math.radians(gap_deg) - half_height
    return Placement(placement.azimuth_deg, math.degrees(math.atan2(center, radius)), width_deg)


def surface_vertices(
    placement: Placement,
    *,
    aspect: float,
    radius: float = RADIUS,
    segments: int = CURVE_SEGMENTS,
) -> np.ndarray:
    """Triangle-strip vertices for one curved screen: (x, y, z, u, v) rows.

    The screen subtends the placement's width of the cylinder centered on its
    azimuth; its height is the arc length over *aspect* (pixel width/height),
    so the video fills it edge to edge without letterboxing, and its center
    rides at the placement's elevation above the horizon.  Columns run left to
    right, two vertices each (upper v=1, then lower v=0), ready for
    GL_TRIANGLE_STRIP.
    """
    if aspect <= 0:
        raise ValueError(f"aspect must be positive, got {aspect}")
    width_rad = math.radians(placement.width_deg)
    half_height = (radius * width_rad / aspect) / 2
    lift = radius * math.tan(math.radians(placement.elevation_deg))
    start = math.radians(placement.azimuth_deg) - width_rad / 2

    rows: list[tuple[float, float, float, float, float]] = []
    for column in range(segments + 1):
        t = column / segments
        azimuth = start + t * width_rad
        x = radius * math.sin(azimuth)
        z = -radius * math.cos(azimuth)
        rows.append((x, lift + half_height, z, t, 1.0))
        rows.append((x, lift - half_height, z, t, 0.0))
    return np.array(rows, dtype=np.float32)
