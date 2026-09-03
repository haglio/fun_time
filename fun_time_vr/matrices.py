"""View/projection math for the OpenXR frame loop — pure numpy, GLSL-free.

Adapted from GenauVR's proven bring-up (genau_vr.projection): the same
row-major layout, uploaded with transpose=GL_TRUE.  The dead 360°-UV helpers
that repo carries were deliberately left behind; the real UV mapping lives in
this app's shaders.
"""
from __future__ import annotations

import math

import numpy as np


def fov_to_projection_matrix(
    angle_left: float,
    angle_right: float,
    angle_up: float,
    angle_down: float,
    near: float,
    far: float,
) -> np.ndarray:
    """Build an OpenGL projection matrix from OpenXR FOV angles (radians)."""
    tan_l = math.tan(angle_left)
    tan_r = math.tan(angle_right)
    tan_u = math.tan(angle_up)
    tan_d = math.tan(angle_down)

    width = tan_r - tan_l
    height = tan_u - tan_d

    mat = np.zeros((4, 4), dtype=np.float32)
    mat[0, 0] = 2.0 / width
    mat[0, 2] = (tan_r + tan_l) / width
    mat[1, 1] = 2.0 / height
    mat[1, 2] = (tan_u + tan_d) / height
    mat[2, 2] = -(far + near) / (far - near)
    mat[2, 3] = -(2.0 * far * near) / (far - near)
    mat[3, 2] = -1.0
    return mat


def _quat_to_rotation_matrix(x: float, y: float, z: float, w: float) -> np.ndarray:
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array([
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
    ], dtype=np.float32)


def yaw_of_orientation(orientation: tuple[float, float, float, float]) -> float:
    """The heading of an (x, y, z, w) orientation: radians about +Y that turn
    world-forward (-Z) onto the orientation's forward, projected level.

    What recentering captures — pitch and roll are deliberately dropped, so a
    scene re-zeroed while glancing at the floor still stands upright.
    """
    x, y, z, w = orientation
    # Third column of the rotation matrix applied to (0, 0, -1).
    forward_x = -(2 * (x * z + w * y))
    forward_z = -(1 - 2 * (x * x + y * y))
    return math.atan2(-forward_x, -forward_z)


def yaw_rotation_matrix(yaw: float) -> np.ndarray:
    """A model matrix rotating the scene *yaw* radians about +Y (row-major,
    like everything here, uploaded with transpose=GL_TRUE)."""
    sin, cos = math.sin(yaw), math.cos(yaw)
    mat = np.eye(4, dtype=np.float32)
    mat[0, 0] = cos
    mat[0, 2] = sin
    mat[2, 0] = -sin
    mat[2, 2] = cos
    return mat


def pitch_rotation_matrix(pitch: float) -> np.ndarray:
    """A model matrix tilting the scene *pitch* radians about +X, nose-up
    positive (row-major, like everything here).

    Applied inside the recentering yaw -- yaw_rotation_matrix(yaw) @
    pitch_rotation_matrix(pitch) -- so the arrangement tilts about its own
    horizontal axis rather than about the world's.  The other order rolls the
    scene whenever the two are both non-zero.
    """
    sin, cos = math.sin(pitch), math.cos(pitch)
    mat = np.eye(4, dtype=np.float32)
    mat[1, 1] = cos
    mat[1, 2] = -sin
    mat[2, 1] = sin
    mat[2, 2] = cos
    return mat


def pose_to_view_matrix(
    position: tuple[float, float, float],
    orientation: tuple[float, float, float, float],
) -> np.ndarray:
    """The view matrix (inverse pose) for an OpenXR pose.

    Callers pass position (0,0,0) for the rotation-only view the video sphere
    needs — a head that translates must not parallax a projected sphere.
    """
    rot = _quat_to_rotation_matrix(*orientation)
    pos = np.array(position, dtype=np.float32)

    mat = np.eye(4, dtype=np.float32)
    mat[:3, :3] = rot.T
    mat[:3, 3] = -rot.T @ pos
    return mat
