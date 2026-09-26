"""The geometry of one screen in the VR scene: where a picture hangs, and the
flat rectangle that carries it, square on to the viewer with its middle on one
cylinder around the head.  Immersive projections (equirect/fisheye) don't use
these at all — they fill the view from a shader — so this module is the whole
of the "windowed" layout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

# The view matrix is rotation-only (no head translation reaches the scene), so
# the radius sets apparent scale only.
RADIUS = 2.0

MAIN_WIDTH_DEG = 72.0


@dataclass(frozen=True)
class Placement:
    azimuth_deg: float
    elevation_deg: float
    width_deg: float


def center_height(placement: Placement, radius: float = RADIUS) -> float:
    return radius * math.tan(math.radians(placement.elevation_deg))


def elevation_at(height: float, radius: float = RADIUS) -> float:
    return math.degrees(math.atan2(height, radius))


def half_width(width_deg: float, radius: float = RADIUS) -> float:
    return radius * math.tan(math.radians(width_deg) / 2.0)


def widened_deg(width_deg: float, by: float) -> float:
    return math.degrees(2.0 * math.atan(math.tan(math.radians(width_deg) / 2.0) * by))


def widened(placement: Placement, by: float) -> Placement:
    return replace(placement, width_deg=widened_deg(placement.width_deg, by))


def turn_deg(from_deg: float, to_deg: float) -> float:
    return (to_deg - from_deg + 180.0) % 360.0 - 180.0


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
    """Pose and size for the compositor quad carrying the rectangle
    :func:`surface_vertices` draws: ``(position, orientation_xyzw, (width,
    height))`` in the reference space's meters."""
    if aspect <= 0:
        raise ValueError(f"aspect must be positive, got {aspect}")
    theta = math.radians(placement.azimuth_deg)
    position = (
        radius * math.sin(theta),
        center_height(placement, radius),
        -radius * math.cos(theta),
    )
    # A rotation about +Y by -theta points the quad's +Z (its front face,
    # per the OpenXR quad-layer convention) back at the viewer.
    orientation = (0.0, math.sin(-theta / 2.0), 0.0, math.cos(theta / 2.0))
    scene = scene_placement_quaternion(scene_yaw_deg, scene_pitch_deg)
    position = _quat_rotate(scene, position)
    orientation = _quat_multiply(scene, orientation)
    width = 2.0 * half_width(placement.width_deg, radius)
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
    lower = center_height(placement, radius) - half_width(placement.width_deg, radius) / aspect
    center = (lower - radius * math.radians(gap_deg)
              - half_width(width_deg, radius) / hanging_aspect)
    return Placement(placement.azimuth_deg, elevation_at(center, radius), width_deg)


_ON_THE_EDGE = 1e-6


def encloses(outer: Placement, inner: Placement, *, outer_aspect: float, inner_aspect: float) -> bool:
    upper_left, lower_left, upper_right, _lower_right = _corners(outer, outer_aspect)
    across, up = upper_right - upper_left, upper_left - lower_left
    middle = (lower_left + upper_right) / 2.0
    away = np.cross(across, up)
    away *= np.sign(np.dot(away, middle))
    for corner in _corners(inner, inner_aspect):
        depth = np.dot(corner, away)
        if depth <= 0.0:
            return False
        seen = corner * np.dot(middle, away) / depth - middle
        if (abs(np.dot(seen, across) / np.dot(across, across)) > 0.5 + _ON_THE_EDGE
                or abs(np.dot(seen, up) / np.dot(up, up)) > 0.5 + _ON_THE_EDGE):
            return False
    return True


def _corners(placement: Placement, aspect: float) -> np.ndarray:
    return surface_vertices(placement, aspect=aspect)[:, :3].astype(np.float64)


def surface_vertices(
    placement: Placement,
    *,
    aspect: float,
    radius: float = RADIUS,
    u: tuple[float, float] = (0.0, 1.0),
    v: tuple[float, float] = (0.0, 1.0),
) -> np.ndarray:
    if aspect <= 0:
        raise ValueError(f"aspect must be positive, got {aspect}")
    theta = math.radians(placement.azimuth_deg)
    half = half_width(placement.width_deg, radius)
    middle = center_height(placement, radius)
    lower, upper = v

    rows: list[tuple[float, float, float, float, float]] = []
    for column in u:
        across = (2 * column - 1) * half
        x = radius * math.sin(theta) + across * math.cos(theta)
        z = -radius * math.cos(theta) + across * math.sin(theta)
        rows.append((x, middle + (2 * upper - 1) * half / aspect, z, column, upper))
        rows.append((x, middle + (2 * lower - 1) * half / aspect, z, column, lower))
    return np.array(rows, dtype=np.float32)
