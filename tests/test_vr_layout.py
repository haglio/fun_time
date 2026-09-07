"""Where the screens hang: the defaults, and the layout remembered between sessions."""
from __future__ import annotations

import json

from fun_time_vr.layout import (
    AZIMUTH_LIMIT_DEG,
    DASH,
    DEFAULT_LAYOUT,
    ELEVATION_LIMIT_DEG,
    LANDSCAPE,
    MAX_WIDTH_DEG,
    MIN_WIDTH_DEG,
    PORTRAIT,
    PRIMARY,
    clamp_placement,
    clamp_width,
    read_layout,
    write_layout,
)
from fun_time_vr.scene import PRIMARY_WIDTH_DEG, Placement


class TestTheDefaults:
    def test_the_primary_is_one_of_the_movable_screens_hanging_dead_ahead(self):
        """It moves and zooms by the same handles the satellites do, so it is in
        the same dict — starting where it has always sat, level and straight on."""
        assert DEFAULT_LAYOUT[PRIMARY] == Placement(0.0, 0.0, PRIMARY_WIDTH_DEG)

    def test_the_satellites_flank_the_primary_landscape_left_portrait_right(self):
        """The sides a desktop session puts them on, so the room reads the same
        in the headset as it does on the monitors."""
        portrait, landscape = DEFAULT_LAYOUT[PORTRAIT], DEFAULT_LAYOUT[LANDSCAPE]

        assert landscape.azimuth_deg < 0 < portrait.azimuth_deg
        assert portrait.azimuth_deg == -landscape.azimuth_deg
        assert portrait.width_deg == landscape.width_deg
        assert portrait.elevation_deg == landscape.elevation_deg

    def test_the_satellites_tuck_inside_the_flush_position(self):
        # First headset run: flush-beside-the-main-player put both satellites in
        # the peripheral vision, so they overlap the main player's edges instead —
        # they draw over it, so overlap costs nothing.
        landscape = DEFAULT_LAYOUT[LANDSCAPE]
        flush = (PRIMARY_WIDTH_DEG + landscape.width_deg) / 2

        assert abs(landscape.azimuth_deg) < flush

    def test_the_satellites_are_smaller_than_the_primary_half(self):
        assert DEFAULT_LAYOUT[LANDSCAPE].width_deg < PRIMARY_WIDTH_DEG / 2

    def test_the_satellites_ride_above_the_horizon(self):
        assert DEFAULT_LAYOUT[LANDSCAPE].elevation_deg > 0

    def test_only_the_screens_a_controller_places_are_in_here(self):
        """The console docks under the main player rather than being placed, so
        it is not one of these -- and a file naming it is ignored, not obeyed."""
        assert set(DEFAULT_LAYOUT) == {PRIMARY, PORTRAIT, LANDSCAPE, DASH}


class TestTheRememberedLayout:
    def test_no_file_yet_reads_as_the_defaults(self, tmp_path):
        assert read_layout(tmp_path / "vr_layout.json") == DEFAULT_LAYOUT

    def test_what_was_written_reads_back(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        moved = {
            **DEFAULT_LAYOUT,
            LANDSCAPE: Placement(azimuth_deg=-20.0, elevation_deg=25.5, width_deg=30.0),
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
            LANDSCAPE: {"azimuth_deg": 5.0, "elevation_deg": 20.0, "width_deg": 24.0},
            PORTRAIT: {"azimuth_deg": "sideways"},
            "basement": {"azimuth_deg": 0.0, "elevation_deg": -60.0, "width_deg": 10.0},
        }), encoding="utf-8")

        layout = read_layout(path)

        assert layout[LANDSCAPE] == Placement(5.0, 20.0, 24.0)
        assert layout[PORTRAIT] == DEFAULT_LAYOUT[PORTRAIT]
        assert layout[PRIMARY] == DEFAULT_LAYOUT[PRIMARY]
        assert "basement" not in layout

    def test_a_zoomed_and_moved_primary_comes_back_next_session(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        zoomed = Placement(azimuth_deg=-12.0, elevation_deg=-4.0, width_deg=210.0)

        assert write_layout(path, {**DEFAULT_LAYOUT, PRIMARY: zoomed})

        assert read_layout(path)[PRIMARY] == zoomed

    def test_a_remembered_placement_is_held_within_the_scene(self, tmp_path):
        """A hand-edited file cannot hang a screen at the viewer's back, at the
        zenith, or too small to grab."""
        path = tmp_path / "vr_layout.json"
        path.write_text(json.dumps({
            LANDSCAPE: {"azimuth_deg": 200.0, "elevation_deg": 89.0, "width_deg": 1.0},
            PORTRAIT: {"azimuth_deg": -200.0, "elevation_deg": -89.0, "width_deg": 4000.0},
        }), encoding="utf-8")

        layout = read_layout(path)

        assert layout[LANDSCAPE] == Placement(
            AZIMUTH_LIMIT_DEG, ELEVATION_LIMIT_DEG, MIN_WIDTH_DEG)
        assert layout[PORTRAIT] == Placement(
            -AZIMUTH_LIMIT_DEG, -ELEVATION_LIMIT_DEG, MAX_WIDTH_DEG)


class TestTheLimits:
    def test_every_default_is_already_within_them(self):
        for placement in DEFAULT_LAYOUT.values():
            assert clamp_placement(placement) == placement

    def test_the_limits_keep_a_screen_in_front_and_off_the_poles(self):
        assert 90 < AZIMUTH_LIMIT_DEG < 180
        assert 45 < ELEVATION_LIMIT_DEG < 90
        assert 0 < MIN_WIDTH_DEG < DEFAULT_LAYOUT[PORTRAIT].width_deg

    def test_a_screen_may_be_pulled_up_to_a_full_wrap_and_no_further(self):
        """Every screen shares one ceiling, and it is the geometry's rather than
        a taste in sizes: at a full turn a screen closes on itself, and past that
        screen_uv can no longer tell one of its edges from the other."""
        assert MAX_WIDTH_DEG == 360.0
        assert clamp_width(4000.0) == MAX_WIDTH_DEG
        assert clamp_width(PRIMARY_WIDTH_DEG * 4) == PRIMARY_WIDTH_DEG * 4
