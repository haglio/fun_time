"""The controller drawn in each hand: a head the laser leaves from, and a handle under it."""
from __future__ import annotations

import numpy as np
import pytest

from fun_time_vr.controller_mesh import (
    HEAD_LENGTH_M,
    HEAD_UNDERSIDE_M,
    controller_faces,
    controllers_strip,
)

_LEVEL = np.eye(3)
_TURNED_AROUND = np.diag([-1.0, 1.0, -1.0])
HEAD, HANDLE = "head", "handle"


def _corners(faces) -> np.ndarray:
    return np.vstack([face.corners for face in faces])


def _square_to_the_ray(faces) -> list:
    return [face for face in faces if np.ptp(face.corners[:, 2]) < 1e-9]


def _parts_in_drawing_order(faces, origin: np.ndarray) -> list[str]:
    """Which part each face is, in the order drawn; the faces lying flat on the
    plane between the two parts are neither, and are left out."""
    order = []
    for face in faces:
        heights = face.corners[:, 1] - origin[1]
        if heights.max() > HEAD_UNDERSIDE_M + 1e-9:
            order.append(HEAD)
        elif heights.min() < HEAD_UNDERSIDE_M - 1e-9:
            order.append(HANDLE)
    return order


class TestTheControllerInTheHand:
    def test_it_reaches_back_toward_him_from_the_tip_the_laser_leaves(self):
        origin = np.array([0.2, -0.3, -0.4])

        corners = _corners(controller_faces(origin, _LEVEL))

        back_from_the_tip = corners[:, 2] - origin[2]  # the scene's forward is -z
        assert back_from_the_tip.min() == pytest.approx(0.0, abs=1e-9)
        assert back_from_the_tip.max() < 0.15

    def test_a_face_turned_away_from_the_eye_is_left_out(self):
        """Nothing sorts the scene by depth, so a face on the far side would be
        painted over the near ones.  The eye is at the scene's center."""
        pointed_away = _square_to_the_ray(controller_faces(np.array([0.0, 0.0, -0.5]), _LEVEL))
        pointed_at_him = _square_to_the_ray(
            controller_faces(np.array([0.0, 0.0, -0.5]), _TURNED_AROUND))

        assert [face.corners[0, 2] for face in pointed_away] == pytest.approx(
            [-0.5 + HEAD_LENGTH_M])
        assert [face.corners[0, 2] for face in pointed_at_him] == pytest.approx([-0.5])

    def test_the_part_nearer_the_eye_is_drawn_over_the_other(self):
        held_low, held_high = np.array([0.0, -0.5, -0.3]), np.array([0.0, 0.5, -0.3])

        looked_down_on = _parts_in_drawing_order(controller_faces(held_low, _LEVEL), held_low)
        looked_up_at = _parts_in_drawing_order(controller_faces(held_high, _LEVEL), held_high)

        assert set(looked_down_on) == set(looked_up_at) == {HEAD, HANDLE}
        assert looked_down_on == sorted(looked_down_on, key=[HANDLE, HEAD].index)
        assert looked_up_at == sorted(looked_up_at, key=[HEAD, HANDLE].index)

    def test_it_is_lit_from_above_without_any_face_going_dark(self):
        """One flat color would show him a silhouette; the shading is what makes
        it a solid.  A face turned from the light still shows against the dark."""
        held_low, held_high = np.array([0.0, -0.5, -0.3]), np.array([0.0, 0.5, -0.3])
        looked_down_on = controller_faces(held_low, _LEVEL)
        looked_up_at = controller_faces(held_high, _LEVEL)

        (top,) = [face for face in looked_down_on if (face.corners[:, 1] - held_low[1]).min() > 0]
        (back,) = _square_to_the_ray(looked_down_on)
        (handle_end,) = [
            face for face in looked_up_at if (face.corners[:, 1] - held_high[1]).max() < -0.09]
        assert 0.3 < handle_end.shade < back.shade < top.shade <= 1.0


def _triangles(strip: np.ndarray) -> list[np.ndarray]:
    return [strip[i:i + 3] for i in range(len(strip) - 2)]


def _area(triangle: np.ndarray) -> float:
    a, b, c = triangle[:, :3].astype(np.float64)
    return float(np.linalg.norm(np.cross(b - a, c - a))) / 2.0


def _face_area(face) -> float:
    a, b, c, d = face.corners
    return float(np.linalg.norm(np.cross(c - a, d - b))) / 2.0


class TestTheStripItIsDrawnAs:
    def test_each_face_is_drawn_whole_in_its_own_shade_and_nothing_between_them(self):
        pose = (np.array([0.1, -0.4, -0.35]), _LEVEL)
        faces = controller_faces(*pose)

        drawn = [triangle for triangle in _triangles(controllers_strip([pose]))
                 if _area(triangle) > 1e-12]

        assert len(drawn) == 2 * len(faces)
        assert sum(map(_area, drawn)) == pytest.approx(sum(map(_face_area, faces)), rel=1e-5)
        for triangle in drawn:
            (face,) = [face for face in faces if all(
                np.isclose(face.corners, corner[:3], atol=1e-6).all(axis=1).any()
                for corner in triangle)]
            assert triangle[:, 3] == pytest.approx([face.shade] * 3)

    def test_the_farther_controller_is_drawn_before_the_nearer_one(self):
        near_on_the_right = (np.array([0.15, -0.3, -0.3]), _LEVEL)
        far_on_the_left = (np.array([-0.15, -0.3, -0.6]), _LEVEL)

        for held in ([near_on_the_right, far_on_the_left], [far_on_the_left, near_on_the_right]):
            strip = controllers_strip(held)

            assert strip[0, 0] < 0.0 < strip[-1, 0]
