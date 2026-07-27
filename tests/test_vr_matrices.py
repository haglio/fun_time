from __future__ import annotations

import math

import numpy as np
import pytest

from fun_time_vr.matrices import (
    fov_to_projection_matrix,
    pose_to_view_matrix,
    yaw_of_orientation,
    yaw_rotation_matrix,
)


class TestFovToProjectionMatrix:
    def test_symmetric_fov_shape(self):
        half = math.radians(45)
        mat = fov_to_projection_matrix(-half, half, half, -half, 0.1, 100.0)
        assert mat.shape == (4, 4)

    def test_symmetric_fov_produces_symmetric_matrix(self):
        half = math.radians(45)
        mat = fov_to_projection_matrix(-half, half, half, -half, 0.1, 100.0)
        # For symmetric FOV: m[0,2] and m[1,2] should be 0 (no off-center shift)
        assert mat[0, 2] == pytest.approx(0.0)
        assert mat[1, 2] == pytest.approx(0.0)

    def test_near_far_encoded_in_matrix(self):
        half = math.radians(45)
        mat = fov_to_projection_matrix(-half, half, half, -half, 0.1, 100.0)
        # m[3,2] should be -1 (perspective divide)
        assert mat[3, 2] == pytest.approx(-1.0)
        # m[2,3] should encode near*far product
        assert mat[2, 3] == pytest.approx(-2.0 * 100.0 * 0.1 / (100.0 - 0.1))

    def test_asymmetric_fov_shifts_center(self):
        # A headset's per-eye FOV is asymmetric; the shift lands in column 2.
        mat = fov_to_projection_matrix(
            math.radians(-50), math.radians(40), math.radians(45), math.radians(-45), 0.1, 100.0
        )
        assert mat[0, 2] != pytest.approx(0.0)


class TestPoseToViewMatrix:
    def test_identity_pose_returns_identity(self):
        mat = pose_to_view_matrix((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))
        np.testing.assert_allclose(mat, np.eye(4), atol=1e-7)

    def test_translation_only(self):
        mat = pose_to_view_matrix((1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0))
        # View matrix inverts the pose, so translation should be negated
        assert mat[0, 3] == pytest.approx(-1.0)
        assert mat[1, 3] == pytest.approx(-2.0)
        assert mat[2, 3] == pytest.approx(-3.0)

    def test_yaw_rotation_turns_forward_toward_x(self):
        # A +90° yaw (about +Y) turns the head from -Z toward -X; the view
        # matrix is the inverse, so world -X lands on the view's forward -Z.
        s = math.sin(math.pi / 4)
        mat = pose_to_view_matrix((0.0, 0.0, 0.0), (0.0, s, 0.0, math.cos(math.pi / 4)))
        world_minus_x = np.array([-1.0, 0.0, 0.0, 1.0], dtype=np.float32)
        viewed = mat @ world_minus_x
        np.testing.assert_allclose(viewed[:3], [0.0, 0.0, -1.0], atol=1e-6)


class TestYaw:
    def test_identity_orientation_has_zero_yaw(self):
        assert yaw_of_orientation((0.0, 0.0, 0.0, 1.0)) == pytest.approx(0.0)

    def test_a_pure_yaw_quaternion_reports_its_own_angle(self):
        for degrees in (-135.0, -30.0, 45.0, 90.0, 170.0):
            half = math.radians(degrees) / 2
            quat = (0.0, math.sin(half), 0.0, math.cos(half))
            assert yaw_of_orientation(quat) == pytest.approx(
                math.radians(degrees), abs=1e-6
            )

    def test_pitch_and_roll_do_not_disturb_the_heading(self):
        # Recentering while glancing down must not tilt the scene: only the
        # level heading survives.  Compose yaw(60°) then pitch(40°): the
        # combined orientation's forward dips, but its heading is still 60°.
        yaw_half = math.radians(60.0) / 2
        pitch_half = math.radians(40.0) / 2
        yaw_quat = np.array([0.0, math.sin(yaw_half), 0.0, math.cos(yaw_half)])
        pitch_quat = np.array([math.sin(pitch_half), 0.0, 0.0, math.cos(pitch_half)])
        x1, y1, z1, w1 = yaw_quat
        x2, y2, z2, w2 = pitch_quat
        combined = (
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        )
        assert yaw_of_orientation(combined) == pytest.approx(math.radians(60.0), abs=1e-6)

    def test_rotation_matrix_carries_forward_onto_the_yaw_heading(self):
        yaw = math.radians(75.0)
        mat = yaw_rotation_matrix(yaw)
        forward = mat @ np.array([0.0, 0.0, -1.0, 1.0], dtype=np.float32)
        np.testing.assert_allclose(
            forward[:3], [-math.sin(yaw), 0.0, -math.cos(yaw)], atol=1e-6
        )

    def test_round_trip_scene_lands_where_the_head_looks(self):
        # The recentering contract end to end: rotate the scene by the head's
        # yaw and the scene's old forward point sits exactly on the head's
        # level heading.
        half = math.radians(-100.0) / 2
        head = (0.0, math.sin(half), 0.0, math.cos(half))
        mat = yaw_rotation_matrix(yaw_of_orientation(head))
        moved = mat @ np.array([0.0, 0.0, -1.0, 1.0], dtype=np.float32)
        heading = math.atan2(-moved[0], -moved[2])
        assert heading == pytest.approx(math.radians(-100.0), abs=1e-6)
