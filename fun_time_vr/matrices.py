from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Fov:
    angle_left: float
    angle_right: float
    angle_up: float
    angle_down: float


def fov_to_projection_matrix(fov: Fov, near: float, far: float) -> np.ndarray:
    tan_l = math.tan(fov.angle_left)
    tan_r = math.tan(fov.angle_right)
    tan_u = math.tan(fov.angle_up)
    tan_d = math.tan(fov.angle_down)

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


def quat_to_rotation_matrix(x: float, y: float, z: float, w: float) -> np.ndarray:
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array([
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
    ], dtype=np.float32)


def yaw_of_orientation(orientation: tuple[float, float, float, float]) -> float:
    x, y, z, w = orientation
    forward_x = -(2 * (x * z + w * y))
    forward_z = -(1 - 2 * (x * x + y * y))
    return math.atan2(-forward_x, -forward_z)


def yaw_rotation_matrix(yaw: float) -> np.ndarray:
    sin, cos = math.sin(yaw), math.cos(yaw)
    mat = np.eye(4, dtype=np.float32)
    mat[0, 0] = cos
    mat[0, 2] = sin
    mat[2, 0] = -sin
    mat[2, 2] = cos
    return mat


def pitch_rotation_matrix(pitch: float) -> np.ndarray:
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
    rot = quat_to_rotation_matrix(*orientation)
    pos = np.array(position, dtype=np.float32)

    mat = np.eye(4, dtype=np.float32)
    mat[:3, :3] = rot.T
    mat[:3, 3] = -rot.T @ pos
    return mat
