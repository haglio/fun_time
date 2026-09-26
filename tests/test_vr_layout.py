"""What a session remembers of where the controllers left each screen.

The spot a screen STARTS in belongs to the unit that hangs it, so a screen
this file answers nothing for is not a screen with no place -- it is one that
was never dragged.  ``tests/test_vr_player.py`` pins those spots."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from fun_time_vr.layout import (
    AZIMUTH_LIMIT_DEG,
    ELEVATION_LIMIT_DEG,
    LANDSCAPE,
    MAIN,
    MAX_WIDTH_DEG,
    MIN_WIDTH_DEG,
    PORTRAIT,
    clamp_width,
    grown,
    nearer,
    read_layout,
    rearranged,
    shown_at,
    write_layout,
)
from fun_time_vr.scene import MAIN_WIDTH_DEG, RADIUS, Placement, surface_vertices


class TestTheRememberedLayout:
    def test_no_file_yet_remembers_nothing(self, tmp_path):
        assert read_layout(tmp_path / "vr_layout.json") == {}

    def test_what_was_written_reads_back(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        moved = {LANDSCAPE: Placement(azimuth_deg=-20.0, elevation_deg=25.5, width_deg=30.0)}

        assert write_layout(path, moved)

        assert read_layout(path) == moved

    def test_a_file_that_cannot_be_read_remembers_nothing(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        path.write_text("{not json", encoding="utf-8")

        assert read_layout(path) == {}

    def test_a_screen_the_file_names_badly_is_remembered_no_better_than_one_it_leaves_out(
            self, tmp_path):
        """Both come back as nothing, which is what sends the screen to the spot
        its own unit keeps for it."""
        path = tmp_path / "vr_layout.json"
        path.write_text(json.dumps({
            LANDSCAPE: {"azimuth_deg": 5.0, "elevation_deg": 20.0, "width_deg": 24.0},
            PORTRAIT: {"azimuth_deg": "sideways"},
        }), encoding="utf-8")

        layout = read_layout(path)

        assert layout == {LANDSCAPE: Placement(5.0, 20.0, 24.0)}

    def test_a_zoomed_and_moved_main_screen_comes_back_next_session(self, tmp_path):
        path = tmp_path / "vr_layout.json"
        zoomed = Placement(azimuth_deg=-12.0, elevation_deg=-4.0, width_deg=110.0)

        assert write_layout(path, {MAIN: zoomed})

        assert read_layout(path)[MAIN] == zoomed

    def test_remembering_nothing_at_all_reads_back_as_nothing(self, tmp_path):
        """What a reset writes: the file says the session moved no screen, so
        every one of them opens where its unit says it does."""
        path = tmp_path / "vr_layout.json"
        assert write_layout(path, {MAIN: Placement(-12.0, -4.0, 110.0)})

        assert write_layout(path, {})

        assert read_layout(path) == {}

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
    def test_the_limits_keep_a_screen_in_front_and_off_the_poles(self):
        assert 90 < AZIMUTH_LIMIT_DEG < 180
        assert 45 < ELEVATION_LIMIT_DEG < 90
        assert 0 < MIN_WIDTH_DEG < MAX_WIDTH_DEG

    def test_a_screen_may_be_pulled_wide_until_its_edges_are_twice_as_far_off_as_its_middle(self):
        """A flat screen's edges run off to infinity at a half turn, and well before
        that its corners are too far away to take hold of."""
        widest = surface_vertices(Placement(0.0, 0.0, MAX_WIDTH_DEG), aspect=16 / 9)

        assert math.hypot(widest[0, 0], widest[0, 2]) == pytest.approx(2 * RADIUS)
        assert clamp_width(4000.0) == MAX_WIDTH_DEG
        assert clamp_width(MAIN_WIDTH_DEG * 1.5) == MAIN_WIDTH_DEG * 1.5


class TestGrowingTheMainPlayer:
    def test_growing_it_widens_it_about_its_own_middle(self):
        assert grown(Placement(10.0, 5.0, 72.0), 1.5) == Placement(10.0, 5.0, 108.0)

    def test_a_grow_stops_at_the_smallest_screen_and_at_the_widest(self):
        assert grown(Placement(0.0, 0.0, 20.0), 0.1).width_deg == MIN_WIDTH_DEG
        assert grown(Placement(0.0, 0.0, 100.0), 2.0).width_deg == MAX_WIDTH_DEG


class TestBringingThePlayersNearer:
    def test_nearer_spreads_and_widens_all_three_about_the_main_player(self):
        main = Placement(10.0, 0.0, 72.0)
        players = {MAIN: main, LANDSCAPE: Placement(-28.0, 10.0, 28.0),
                   PORTRAIT: Placement(48.0, 10.0, 28.0)}

        assert nearer(players, 1.5, about=main) == {
            MAIN: Placement(10.0, 0.0, 108.0),
            LANDSCAPE: Placement(-47.0, 15.0, 42.0),
            PORTRAIT: Placement(67.0, 15.0, 42.0),
        }

    def test_the_players_stop_together_when_one_would_pass_the_edge_of_the_scene(self):
        main = Placement(0.0, 0.0, 72.0)
        players = {MAIN: main, PORTRAIT: Placement(100.0, 0.0, 28.0)}

        moved = nearer(players, 2.0, about=main)

        assert moved[PORTRAIT].azimuth_deg == pytest.approx(AZIMUTH_LIMIT_DEG)
        assert moved[MAIN].width_deg == pytest.approx(72.0 * 1.5)

    def test_the_players_stop_together_when_one_would_pass_the_top_of_the_scene(self):
        main = Placement(0.0, 0.0, 72.0)
        players = {MAIN: main, PORTRAIT: Placement(20.0, 50.0, 28.0)}

        moved = nearer(players, 2.0, about=main)

        assert moved[PORTRAIT].elevation_deg == pytest.approx(ELEVATION_LIMIT_DEG)
        assert moved[MAIN].width_deg == pytest.approx(72.0 * 1.5)

    def test_the_players_stop_together_when_one_would_grow_past_the_widest(self):
        main = Placement(0.0, 0.0, 100.0)
        players = {MAIN: main, PORTRAIT: Placement(10.0, 0.0, 28.0)}

        moved = nearer(players, 2.0, about=main)

        assert moved[MAIN].width_deg == pytest.approx(MAX_WIDTH_DEG)
        assert moved[PORTRAIT].width_deg == pytest.approx(28.0 * 1.2)

    def test_further_off_they_stop_together_when_one_would_shrink_past_the_smallest(self):
        main = Placement(0.0, 0.0, 72.0)
        players = {MAIN: main, PORTRAIT: Placement(30.0, 0.0, 20.0)}

        moved = nearer(players, 0.25, about=main)

        assert moved[PORTRAIT] == Placement(15.0, 0.0, MIN_WIDTH_DEG)
        assert moved[MAIN].width_deg == pytest.approx(36.0)


class TestWhereTheControllersLeaveThePlayers:
    _PLAYERS = {
        MAIN: Placement(0.0, 0.0, 72.0),
        LANDSCAPE: Placement(-38.0, 10.0, 28.0),
        PORTRAIT: Placement(38.0, 10.0, 28.0),
    }

    def test_the_stick_alone_grows_the_main_player_and_the_satellites_stay(self):
        moved = rearranged(self._PLAYERS, grow=1.5, nearer_by=1.0)

        assert moved == {MAIN: Placement(0.0, 0.0, 108.0)}

    def test_the_stick_with_the_trigger_held_brings_all_three_nearer(self):
        moved = rearranged(self._PLAYERS, grow=1.0, nearer_by=1.5)

        assert moved == nearer(self._PLAYERS, 1.5, about=self._PLAYERS[MAIN])

    def test_a_still_stick_leaves_every_player_where_it_was(self):
        assert rearranged(self._PLAYERS, grow=1.0, nearer_by=1.0) == {}


def _area(placement: Placement, aspect: float) -> float:
    corners = surface_vertices(placement, aspect=aspect)
    return float(np.ptp(corners[:, 0]) * np.ptp(corners[:, 1]))


class TestAPictureKeepsItsArea:
    def test_a_clip_taller_than_it_is_wide_covers_what_a_widescreen_one_does_in_the_main_slot(
            self):
        slot = Placement(0.0, 0.0, 80.0)

        assert _area(shown_at(MAIN, slot, 9 / 16), 9 / 16) == pytest.approx(
            _area(shown_at(MAIN, slot, 16 / 9), 16 / 9))

    @pytest.mark.parametrize("name, usual", [
        (MAIN, 16 / 9), (LANDSCAPE, 16 / 9), (PORTRAIT, 9 / 16)])
    def test_a_picture_of_its_screens_usual_shape_is_as_wide_as_the_screen_was_left(
            self, name, usual):
        left = Placement(30.0, 20.0, 38.0)

        assert shown_at(name, left, usual).width_deg == pytest.approx(left.width_deg)

