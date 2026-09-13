from __future__ import annotations

import math

import numpy as np
import pytest

from fun_time_vr.matrices import pitch_rotation_matrix, yaw_rotation_matrix
from fun_time_vr.scene import (
    PRIMARY_WIDTH_DEG,
    RADIUS,
    Placement,
    attached_below,
    quad_layer_placement,
    surface_vertices,
)


def _rotate_by_quat(quat, vec):
    x, y, z, w = quat
    vx, vy, vz = vec
    # Rotation matrix rows applied to vec (standard quaternion rotation).
    return (
        (1 - 2 * (y * y + z * z)) * vx + 2 * (x * y - w * z) * vy + 2 * (x * z + w * y) * vz,
        2 * (x * y + w * z) * vx + (1 - 2 * (x * x + z * z)) * vy + 2 * (y * z - w * x) * vz,
        2 * (x * z - w * y) * vx + 2 * (y * z + w * x) * vy + (1 - 2 * (x * x + y * y)) * vz,
    )


def _azimuth_deg(x: float, z: float) -> float:
    return math.degrees(math.atan2(x, -z))


_A_SATELLITE = Placement(azimuth_deg=38.0, elevation_deg=10.0, width_deg=28.0)


class TestSurfaceVertices:
    def test_the_strip_is_one_quad_of_position_and_texture_rows(self):
        verts = surface_vertices(Placement(0.0, 0.0, 36.0), aspect=16 / 9)
        assert verts.shape == (4, 5)
        assert verts.dtype == np.float32

    def test_it_faces_the_viewer_square_on_with_its_middle_at_the_radius(self):
        verts = surface_vertices(_A_SATELLITE, aspect=9 / 16)[:, :3]
        upper_left, lower_left, upper_right, _lower_right = verts
        middle = verts.mean(axis=0)
        facing = np.cross(upper_right - upper_left, upper_left - lower_left)

        assert math.hypot(middle[0], middle[2]) == pytest.approx(RADIUS, rel=1e-5)
        toward_the_viewer = np.array([-middle[0], 0.0, -middle[2]]) / RADIUS
        np.testing.assert_allclose(facing / np.linalg.norm(facing), toward_the_viewer, atol=1e-5)

    def test_columns_span_the_angular_width_around_the_center(self):
        verts = surface_vertices(Placement(54.0, 0.0, 36.0), aspect=16 / 9)
        azimuths = [_azimuth_deg(x, z) for x, z in zip(verts[::2, 0], verts[::2, 2])]
        assert azimuths[0] == pytest.approx(36.0, abs=1e-4)
        assert azimuths[-1] == pytest.approx(72.0, abs=1e-4)

    def test_u_runs_left_to_right_and_v_low_to_high(self):
        verts = surface_vertices(Placement(0.0, 0.0, 72.0), aspect=16 / 9)
        assert verts[0, 3] == pytest.approx(0.0)   # leftmost column u
        assert verts[-1, 3] == pytest.approx(1.0)  # rightmost column u
        upper, lower = verts[0], verts[1]
        assert upper[1] > lower[1]
        assert upper[4] == pytest.approx(1.0)
        assert lower[4] == pytest.approx(0.0)

    @pytest.mark.parametrize("aspect", [16 / 9, 9 / 16])
    def test_its_shape_is_the_videos_so_the_picture_fills_it_edge_to_edge(self, aspect):
        verts = surface_vertices(Placement(0.0, 0.0, 36.0), aspect=aspect)
        width = verts[:, 0].max() - verts[:, 0].min()
        height = verts[:, 1].max() - verts[:, 1].min()

        assert width / height == pytest.approx(aspect, rel=1e-5)

    def test_the_center_rides_at_the_elevation(self):
        verts = surface_vertices(_A_SATELLITE, aspect=9 / 16)
        center_y = (verts[:, 1].max() + verts[:, 1].min()) / 2
        assert center_y == pytest.approx(
            RADIUS * math.tan(math.radians(_A_SATELLITE.elevation_deg)), rel=1e-5
        )

    def test_the_screen_is_flat_not_curved(self):
        verts = surface_vertices(Placement(0.0, 0.0, 72.0), aspect=16 / 9)[:, :3]

        assert np.linalg.matrix_rank(verts - verts[0], tol=1e-5) == 2

    def test_degenerate_aspect_is_rejected(self):
        with pytest.raises(ValueError):
            surface_vertices(Placement(0.0, 0.0, 36.0), aspect=0.0)


class TestQuadLayerPlacement:
    def test_the_quad_is_the_very_rectangle_the_eye_pass_draws(self):
        position, orientation, (width, height) = quad_layer_placement(_A_SATELLITE, aspect=16 / 9)
        right = np.array(_rotate_by_quat(orientation, (1.0, 0.0, 0.0)))
        up = np.array(_rotate_by_quat(orientation, (0.0, 1.0, 0.0)))
        corners = [np.array(position) + right * width * (u - 0.5) + up * height * (v - 0.5)
                   for u, v in ((0.0, 1.0), (0.0, 0.0), (1.0, 1.0), (1.0, 0.0))]

        np.testing.assert_allclose(
            surface_vertices(_A_SATELLITE, aspect=16 / 9)[:, :3], corners, atol=1e-5)

    def test_center_sits_where_the_screen_centers(self):
        position, _orientation, _size = quad_layer_placement(_A_SATELLITE, aspect=16 / 9)
        assert _azimuth_deg(position[0], position[2]) == pytest.approx(
            _A_SATELLITE.azimuth_deg, abs=1e-5)
        assert math.hypot(position[0], position[2]) == pytest.approx(RADIUS, rel=1e-6)
        assert position[1] == pytest.approx(
            RADIUS * math.tan(math.radians(_A_SATELLITE.elevation_deg)), rel=1e-6
        )

    def test_quad_faces_the_viewer(self):
        # The OpenXR quad convention shows the +Z face, so the pose's +Z must
        # point from the screen's center back at the origin.
        position, orientation, _size = quad_layer_placement(
            Placement(-38.0, 0.0, 28.0), aspect=1.0)
        front = _rotate_by_quat(orientation, (0.0, 0.0, 1.0))
        toward_viewer = (-position[0] / RADIUS, 0.0, -position[2] / RADIUS)
        assert front == pytest.approx(toward_viewer, abs=1e-6)

    def test_quad_subtends_the_screen_width(self):
        _position, _orientation, (width, _height) = quad_layer_placement(
            Placement(0.0, 0.0, PRIMARY_WIDTH_DEG), aspect=16 / 9,
        )
        subtended = 2 * math.degrees(math.atan((width / 2) / RADIUS))
        assert subtended == pytest.approx(PRIMARY_WIDTH_DEG, abs=1e-6)

    def test_height_follows_the_aspect_ratio(self):
        _position, _orientation, (width, height) = quad_layer_placement(
            Placement(0.0, 0.0, 36.0), aspect=9 / 16,
        )
        assert height == pytest.approx(width / (9 / 16), rel=1e-6)

    def test_orientation_is_yaw_only_and_unit_length(self):
        # The screens hang untilted whatever their elevation, and so must their
        # quads, or a lifted satellite would lean back.
        _position, orientation, _size = quad_layer_placement(_A_SATELLITE, aspect=16 / 9)
        x, y, z, w = orientation
        assert x == 0.0 and z == 0.0
        assert math.hypot(y, w) == pytest.approx(1.0, rel=1e-9)

    def test_degenerate_aspect_is_rejected(self):
        with pytest.raises(ValueError):
            quad_layer_placement(Placement(0.0, 0.0, 36.0), aspect=-1.0)

    def test_tilting_lifts_the_quad_and_keeps_it_facing_the_viewer(self):
        # Tilting is not the same as raising: the screen swings up the sphere
        # and leans back, so its face still points at the eye rather than at
        # the ceiling.
        pitch = 30.0
        position, orientation, _size = quad_layer_placement(
            Placement(0.0, 0.0, PRIMARY_WIDTH_DEG), aspect=16 / 9, scene_pitch_deg=pitch,
        )
        assert position == pytest.approx(
            (0.0, RADIUS * math.sin(math.radians(pitch)),
             -RADIUS * math.cos(math.radians(pitch))), abs=1e-6,
        )
        front = _rotate_by_quat(orientation, (0.0, 0.0, 1.0))
        toward_viewer = tuple(-c / RADIUS for c in position)
        assert front == pytest.approx(toward_viewer, abs=1e-6)

    def test_the_quad_lands_where_the_eye_pass_would_draw_the_screen(self):
        # The two render paths place the screens from one fact by two routes —
        # this quaternion and the matrix product the eye pass multiplies in.
        # A satellite off the center line is where a disagreement in the
        # composition order would show, so that is what this pins.
        yaw, pitch = 40.0, -25.0
        position, _orientation, _size = quad_layer_placement(
            _A_SATELLITE, aspect=16 / 9, scene_yaw_deg=yaw, scene_pitch_deg=pitch,
        )
        untilted, _o, _s = quad_layer_placement(_A_SATELLITE, aspect=16 / 9)
        scene = yaw_rotation_matrix(math.radians(yaw)) @ pitch_rotation_matrix(
            math.radians(pitch)
        )
        expected = scene @ np.array([*untilted, 1.0], dtype=np.float32)
        np.testing.assert_allclose(position, expected[:3], atol=1e-6)


class TestAttachedBelow:
    """A strip hanging under a screen's lower edge, centered on it: the satellite's
    HUD, which follows the picture wherever it is dragged and however it is
    resized."""

    _PICTURE = Placement(azimuth_deg=38.0, elevation_deg=10.0, width_deg=28.0)

    def test_it_is_centered_under_the_screen_at_the_width_it_is_given(self):
        hud = attached_below(self._PICTURE, aspect=9 / 16, width_deg=20.0, hanging_aspect=2.0)

        assert hud.azimuth_deg == self._PICTURE.azimuth_deg
        assert hud.width_deg == 20.0

    def test_its_top_edge_meets_the_screens_lower_edge(self):
        hud = attached_below(self._PICTURE, aspect=9 / 16, width_deg=20.0, hanging_aspect=2.0)
        picture = surface_vertices(self._PICTURE, aspect=9 / 16)
        strip = surface_vertices(hud, aspect=2.0)

        assert strip[:, 1].max() == pytest.approx(picture[:, 1].min(), abs=1e-6)

    def test_a_gap_holds_it_off_the_edge_by_that_arc(self):
        flush = attached_below(self._PICTURE, aspect=9 / 16, width_deg=20.0, hanging_aspect=2.0)
        spaced = attached_below(self._PICTURE, aspect=9 / 16, width_deg=20.0, hanging_aspect=2.0,
                                gap_deg=1.0)
        flush_top = surface_vertices(flush, aspect=2.0)[:, 1].max()
        spaced_top = surface_vertices(spaced, aspect=2.0)[:, 1].max()

        assert flush_top - spaced_top == pytest.approx(RADIUS * math.radians(1.0), abs=1e-6)
