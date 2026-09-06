"""Where the screens hang: the defaults, and the layout remembered between sessions."""
from __future__ import annotations

import json

from fun_time_vr.layout import (
    AZIMUTH_LIMIT_DEG,
    DEFAULT_LAYOUT,
    ELEVATION_LIMIT_DEG,
    LANDSCAPE,
    MAX_WIDTH_DEG,
    MIN_WIDTH_DEG,
    PANEL,
    PORTRAIT,
    clamp_placement,
    read_layout,
    write_layout,
)
from fun_time_vr.scene import PRIMARY_WIDTH_DEG, Placement


class TestTheDefaults:
    def test_the_satellites_flank_the_primary_portrait_left_landscape_right(self):
        portrait, landscape = DEFAULT_LAYOUT[PORTRAIT], DEFAULT_LAYOUT[LANDSCAPE]

        assert portrait.azimuth_deg < 0 < landscape.azimuth_deg
        assert portrait.azimuth_deg == -landscape.azimuth_deg
        assert portrait.width_deg == landscape.width_deg
        assert portrait.elevation_deg == landscape.elevation_deg

    def test_the_satellites_tuck_inside_the_flush_position(self):
        # First headset run: flush-beside-the-main-player put both satellites in
        # the peripheral vision, so they overlap the main player's edges instead —
        # they draw over it, so overlap costs nothing.
        landscape = DEFAULT_LAYOUT[LANDSCAPE]
        flush = (PRIMARY_WIDTH_DEG + landscape.width_deg) / 2

        assert landscape.azimuth_deg < flush

    def test_the_satellites_are_smaller_than_the_primary_half(self):
        assert DEFAULT_LAYOUT[LANDSCAPE].width_deg < PRIMARY_WIDTH_DEG / 2

    def test_the_satellites_ride_above_the_horizon(self):
        assert DEFAULT_LAYOUT[LANDSCAPE].elevation_deg > 0

    def test_the_panel_hangs_above_the_primarys_top_edge(self):
        """A 16:9 primary spanning PRIMARY_WIDTH_DEG is this tall; the panel's
        center sits above its edge, so it covers neither the picture nor the
        action, which sits low in an immersive one."""
        primary_half_height_deg = PRIMARY_WIDTH_DEG / (16 / 9) / 2

        assert primary_half_height_deg < DEFAULT_LAYOUT[PANEL].elevation_deg
        assert DEFAULT_LAYOUT[PANEL].azimuth_deg == 0

    def test_the_panel_is_narrower_than_the_primary(self):
        assert DEFAULT_LAYOUT[PANEL].width_deg < PRIMARY_WIDTH_DEG / 2


class TestTheRememberedLayout:
    def test_no_file_yet_reads_as_the_defaults(self, tmp_path):
        assert read_layout(tmp_path / "vr_layout.json") == DEFAULT_LAYOUT

    def test_what_was_written_reads_back(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        moved = {
            **DEFAULT_LAYOUT,
            PANEL: Placement(azimuth_deg=-20.0, elevation_deg=25.5, width_deg=30.0),
        }

        assert write_layout(path, moved)

        assert read_layout(path) == moved

    def test_a_file_that_cannot_be_read_falls_back_to_the_defaults(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        path.write_text("{not json", encoding="utf-8")

        assert read_layout(path) == DEFAULT_LAYOUT

    def test_a_screen_the_file_does_not_name_or_names_badly_keeps_its_default(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        path.write_text(json.dumps({
            PANEL: {"azimuth_deg": 5.0, "elevation_deg": 20.0, "width_deg": 24.0},
            PORTRAIT: {"azimuth_deg": "sideways"},
            "basement": {"azimuth_deg": 0.0, "elevation_deg": -60.0, "width_deg": 10.0},
        }), encoding="utf-8")

        layout = read_layout(path)

        assert layout[PANEL] == Placement(5.0, 20.0, 24.0)
        assert layout[PORTRAIT] == DEFAULT_LAYOUT[PORTRAIT]
        assert layout[LANDSCAPE] == DEFAULT_LAYOUT[LANDSCAPE]
        assert "basement" not in layout

    def test_a_remembered_placement_is_held_within_the_scene(self, tmp_path):
        """A hand-edited file cannot hang a screen at the viewer's back, at the
        zenith, or too small to grab."""
        path = tmp_path / "vr_layout.json"
        path.write_text(json.dumps({
            PANEL: {"azimuth_deg": 200.0, "elevation_deg": 89.0, "width_deg": 1.0},
            PORTRAIT: {"azimuth_deg": -200.0, "elevation_deg": -89.0, "width_deg": 400.0},
        }), encoding="utf-8")

        layout = read_layout(path)

        assert layout[PANEL] == Placement(AZIMUTH_LIMIT_DEG, ELEVATION_LIMIT_DEG, MIN_WIDTH_DEG)
        assert layout[PORTRAIT] == Placement(
            -AZIMUTH_LIMIT_DEG, -ELEVATION_LIMIT_DEG, MAX_WIDTH_DEG)


class TestTheLimits:
    def test_every_default_is_already_within_them(self):
        for placement in DEFAULT_LAYOUT.values():
            assert clamp_placement(placement) == placement

    def test_the_limits_keep_a_screen_in_front_and_off_the_poles(self):
        assert 90 < AZIMUTH_LIMIT_DEG < 180
        assert 45 < ELEVATION_LIMIT_DEG < 90
        assert 0 < MIN_WIDTH_DEG < DEFAULT_LAYOUT[PANEL].width_deg
        assert PRIMARY_WIDTH_DEG <= MAX_WIDTH_DEG < 180
