from __future__ import annotations

import math

import numpy as np
import pytest

from fun_time_vr.matrices import fov_to_projection_matrix, pose_to_view_matrix


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
