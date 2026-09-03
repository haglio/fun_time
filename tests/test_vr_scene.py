from __future__ import annotations

import math

import numpy as np
import pytest

from fun_time_vr.matrices import pitch_rotation_matrix, yaw_rotation_matrix
from fun_time_vr.scene import (
    PRIMARY_WIDTH_DEG,
    RADIUS,
    SATELLITE_ELEVATION_DEG,
    SATELLITE_WIDTH_DEG,
    quad_layer_placement,
    satellite_center_azimuth,
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


class TestLayout:
    def test_satellites_tuck_inside_the_flush_position(self):
        # First headset run: flush-beside-the-main-player put both satellites in
        # the peripheral vision, so they overlap the main player's edges instead —
        # they draw over it, so overlap costs nothing.
        flush = (PRIMARY_WIDTH_DEG + SATELLITE_WIDTH_DEG) / 2
        assert 0 < satellite_center_azimuth("landscape") < flush
        assert satellite_center_azimuth("portrait") == -satellite_center_azimuth("landscape")

    def test_satellites_are_smaller_than_the_primary_half(self):
        assert SATELLITE_WIDTH_DEG < PRIMARY_WIDTH_DEG / 2

    def test_satellites_ride_above_the_horizon(self):
        assert SATELLITE_ELEVATION_DEG > 0
        verts = surface_vertices(
            satellite_center_azimuth("portrait"), SATELLITE_WIDTH_DEG,
            aspect=9 / 16, center_elevation_deg=SATELLITE_ELEVATION_DEG,
        )
        center_y = (verts[:, 1].max() + verts[:, 1].min()) / 2
        assert center_y == pytest.approx(
            RADIUS * math.tan(math.radians(SATELLITE_ELEVATION_DEG)), rel=1e-5
        )

    def test_unknown_side_is_a_hard_error(self):
        with pytest.raises(KeyError):
            satellite_center_azimuth("basement")


class TestSurfaceVertices:
    def test_strip_has_two_vertices_per_column(self):
        verts = surface_vertices(0.0, 36.0, aspect=16 / 9, segments=8)
        assert verts.shape == (18, 5)
        assert verts.dtype == np.float32

    def test_every_vertex_sits_on_the_cylinder(self):
        verts = surface_vertices(-54.0, 36.0, aspect=9 / 16)
        radii = np.sqrt(verts[:, 0] ** 2 + verts[:, 2] ** 2)
        np.testing.assert_allclose(radii, RADIUS, atol=1e-5)

    def test_columns_span_the_angular_width_around_the_center(self):
        verts = surface_vertices(54.0, 36.0, aspect=16 / 9, segments=4)
        azimuths = [_azimuth_deg(x, z) for x, z in zip(verts[::2, 0], verts[::2, 2])]
        assert azimuths[0] == pytest.approx(36.0, abs=1e-4)
        assert azimuths[-1] == pytest.approx(72.0, abs=1e-4)

    def test_u_runs_left_to_right_and_v_bottom_to_top(self):
        verts = surface_vertices(0.0, 72.0, aspect=16 / 9, segments=4)
        assert verts[0, 3] == pytest.approx(0.0)   # leftmost column u
        assert verts[-1, 3] == pytest.approx(1.0)  # rightmost column u
        top, bottom = verts[0], verts[1]
        assert top[1] > bottom[1]
        assert top[4] == pytest.approx(1.0)
        assert bottom[4] == pytest.approx(0.0)

    def test_height_follows_the_aspect_ratio(self):
        # The screen's height is its arc length over the pixel aspect, so a
        # portrait video hangs tall and a widescreen one shallow.
        wide = surface_vertices(0.0, 36.0, aspect=16 / 9)
        tall = surface_vertices(0.0, 36.0, aspect=9 / 16)
        arc = RADIUS * math.radians(36.0)
        assert wide[:, 1].max() - wide[:, 1].min() == pytest.approx(arc / (16 / 9), rel=1e-5)
        assert tall[:, 1].max() - tall[:, 1].min() == pytest.approx(arc / (9 / 16), rel=1e-5)

    def test_the_screen_is_gently_curved_not_flat(self):
        verts = surface_vertices(0.0, 72.0, aspect=16 / 9, segments=8)
        z = verts[::2, 2]
        # The middle of the arc bows away from the chord between the edges.
        assert z[len(z) // 2] < z[0]
        assert z[len(z) // 2] == pytest.approx(-RADIUS, abs=1e-5)

    def test_degenerate_aspect_is_rejected(self):
        with pytest.raises(ValueError):
            surface_vertices(0.0, 36.0, aspect=0.0)


class TestQuadLayerPlacement:
    def test_center_sits_where_the_curved_screen_centers(self):
        azimuth = satellite_center_azimuth("landscape")
        position, _orientation, _size = quad_layer_placement(
            azimuth, SATELLITE_WIDTH_DEG, aspect=16 / 9,
            center_elevation_deg=SATELLITE_ELEVATION_DEG,
        )
        assert _azimuth_deg(position[0], position[2]) == pytest.approx(azimuth, abs=1e-5)
        assert math.hypot(position[0], position[2]) == pytest.approx(RADIUS, rel=1e-6)
        assert position[1] == pytest.approx(
            RADIUS * math.tan(math.radians(SATELLITE_ELEVATION_DEG)), rel=1e-6
        )

    def test_quad_faces_the_viewer(self):
        # The OpenXR quad convention shows the +Z face, so the pose's +Z must
        # point from the screen's center back at the origin.
        position, orientation, _size = quad_layer_placement(-38.0, 28.0, aspect=1.0)
        front = _rotate_by_quat(orientation, (0.0, 0.0, 1.0))
        toward_viewer = (-position[0] / RADIUS, 0.0, -position[2] / RADIUS)
        assert front == pytest.approx(toward_viewer, abs=1e-6)

    def test_quad_subtends_the_screen_width(self):
        _position, _orientation, (width, _height) = quad_layer_placement(
            0.0, PRIMARY_WIDTH_DEG, aspect=16 / 9,
        )
        subtended = 2 * math.degrees(math.atan((width / 2) / RADIUS))
        assert subtended == pytest.approx(PRIMARY_WIDTH_DEG, abs=1e-6)

    def test_height_follows_the_aspect_ratio(self):
        _position, _orientation, (width, height) = quad_layer_placement(
            0.0, 36.0, aspect=9 / 16,
        )
        assert height == pytest.approx(width / (9 / 16), rel=1e-6)

    def test_orientation_is_yaw_only_and_unit_length(self):
        # The curved screens hang untilted whatever their elevation; the flat
        # stand-ins must match, or a lifted satellite would lean back.
        _position, orientation, _size = quad_layer_placement(
            38.0, 28.0, aspect=16 / 9, center_elevation_deg=10.0,
        )
        x, y, z, w = orientation
        assert x == 0.0 and z == 0.0
        assert math.hypot(y, w) == pytest.approx(1.0, rel=1e-9)

    def test_degenerate_aspect_is_rejected(self):
        with pytest.raises(ValueError):
            quad_layer_placement(0.0, 36.0, aspect=-1.0)

    def test_tilting_lifts_the_quad_and_keeps_it_facing_the_viewer(self):
        # Tilting is not the same as raising: the screen swings up the sphere
        # and leans back, so its face still points at the eye rather than at
        # the ceiling.
        pitch = 30.0
        position, orientation, _size = quad_layer_placement(
            0.0, PRIMARY_WIDTH_DEG, aspect=16 / 9, scene_pitch_deg=pitch,
        )
        assert position == pytest.approx(
            (0.0, RADIUS * math.sin(math.radians(pitch)),
             -RADIUS * math.cos(math.radians(pitch))), abs=1e-6,
        )
        front = _rotate_by_quat(orientation, (0.0, 0.0, 1.0))
        toward_viewer = tuple(-c / RADIUS for c in position)
        assert front == pytest.approx(toward_viewer, abs=1e-6)

    def test_the_quad_lands_where_the_eye_pass_would_draw_the_curved_screen(self):
        # The two render paths place the screens from one fact by two routes —
        # this quaternion and the matrix product the eye pass multiplies in.
        # A satellite off the center line is where a disagreement in the
        # composition order would show, so that is what this pins.
        yaw, pitch = 40.0, -25.0
        azimuth = satellite_center_azimuth("landscape")
        position, _orientation, _size = quad_layer_placement(
            azimuth, SATELLITE_WIDTH_DEG, aspect=16 / 9,
            center_elevation_deg=SATELLITE_ELEVATION_DEG,
            scene_yaw_deg=yaw, scene_pitch_deg=pitch,
        )
        untilted, _o, _s = quad_layer_placement(
            azimuth, SATELLITE_WIDTH_DEG, aspect=16 / 9,
            center_elevation_deg=SATELLITE_ELEVATION_DEG,
        )
        scene = yaw_rotation_matrix(math.radians(yaw)) @ pitch_rotation_matrix(
            math.radians(pitch)
        )
        expected = scene @ np.array([*untilted, 1.0], dtype=np.float32)
        np.testing.assert_allclose(position, expected[:3], atol=1e-6)

