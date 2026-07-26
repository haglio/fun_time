from __future__ import annotations

import math

import numpy as np
import pytest

from fun_time_vr.scene import (
    PRIMARY_WIDTH_DEG,
    RADIUS,
    SATELLITE_WIDTH_DEG,
    satellite_center_azimuth,
    surface_vertices,
)


def _azimuth_deg(x: float, z: float) -> float:
    return math.degrees(math.atan2(x, -z))


class TestLayout:
    def test_primary_takes_the_middle_half_and_each_satellite_a_quarter(self):
        # The user's spec: half the view for the primary in the middle, about a
        # quarter each for the satellites beside it.
        assert PRIMARY_WIDTH_DEG == 2 * SATELLITE_WIDTH_DEG

    def test_satellites_sit_flush_against_the_primary_edges(self):
        # portrait left of the primary, landscape right, edge to edge.
        expected = (PRIMARY_WIDTH_DEG + SATELLITE_WIDTH_DEG) / 2
        assert satellite_center_azimuth("portrait") == -expected
        assert satellite_center_azimuth("landscape") == expected

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
