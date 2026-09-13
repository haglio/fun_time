from __future__ import annotations

import math
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np

HEAD_LENGTH_M = 0.07
HEAD_UNDERSIDE_M = -0.016  # the plane between the two parts: the head above it, the handle below
HANDLE_LENGTH_M = 0.095
HANDLE_RAKE_DEG = 25.0


def _rectangle_at_z(half_width: float, low: float, high: float, z: float) -> list[tuple]:
    return [(-half_width, low, z), (half_width, low, z),
            (half_width, high, z), (-half_width, high, z)]


def _rectangle_at_y(half_width: float, near: float, far: float, y: float) -> list[tuple]:
    return [(-half_width, y, near), (half_width, y, near),
            (half_width, y, far), (-half_width, y, far)]


_RAKE = HANDLE_LENGTH_M * np.array([
    0.0, -math.cos(math.radians(HANDLE_RAKE_DEG)), math.sin(math.radians(HANDLE_RAKE_DEG))])
_HEAD = np.array(_rectangle_at_z(0.014, HEAD_UNDERSIDE_M, 0.010, 0.0)
                 + _rectangle_at_z(0.020, HEAD_UNDERSIDE_M, 0.016, HEAD_LENGTH_M))
_HANDLE = np.vstack([np.array(_rectangle_at_y(0.015, 0.030, 0.068, HEAD_UNDERSIDE_M)),
                     np.array(_rectangle_at_y(0.013, 0.033, 0.065, HEAD_UNDERSIDE_M)) + _RAKE])
_FACES = ((0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))


def _outward_normals(part: np.ndarray) -> np.ndarray:
    normals = []
    for face in _FACES:
        a, b, _c, d = part[list(face)]
        normal = np.cross(b - a, d - a)
        if np.dot(normal, a - part.mean(axis=0)) < 0.0:
            normal = -normal
        normals.append(normal / np.linalg.norm(normal))
    return np.array(normals)


_PARTS = tuple((part, _outward_normals(part)) for part in (_HANDLE, _HEAD))

_LIGHT = np.array([0.3, 1.0, 0.5]) / np.linalg.norm([0.3, 1.0, 0.5])  # overhead, over his shoulder
AMBIENT_SHADE = 0.45


class Face(NamedTuple):
    corners: np.ndarray
    shade: float


def _shade(normal: np.ndarray) -> float:
    return AMBIENT_SHADE + (1.0 - AMBIENT_SHADE) * max(0.0, float(np.dot(normal, _LIGHT)))


def controller_faces(origin: np.ndarray, rotation: np.ndarray) -> list[Face]:
    rotation = np.asarray(rotation)
    eye = -(np.asarray(origin) @ rotation)  # in the controller's own frame
    nearer_part_last = _PARTS if eye[1] >= HEAD_UNDERSIDE_M else _PARTS[::-1]
    faces = []
    for part, normals in nearer_part_last:
        corners = part @ rotation.T + origin
        for face, normal in zip(_FACES, normals @ rotation.T):
            quad = corners[list(face)]
            if np.dot(normal, quad[0]) < 0.0:  # turned toward the eye at the scene's center
                faces.append(Face(quad, _shade(normal)))
    return faces


def controllers_strip(poses: Sequence[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    rows: list[tuple] = []
    for origin, rotation in sorted(poses, key=lambda pose: np.linalg.norm(pose[0]), reverse=True):
        for face in controller_faces(origin, rotation):
            a, b, c, d = ((*corner, face.shade, 0.0) for corner in face.corners)
            if rows:  # repeated vertices: the triangles joining two faces have no area
                rows += [rows[-1], a]
            rows += [a, b, d, c]
    return np.array(rows, dtype=np.float32).reshape(-1, 5)
