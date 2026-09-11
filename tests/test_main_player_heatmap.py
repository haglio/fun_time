"""The heatmap's two halves, tested as two.

The arithmetic asks how much travel lands in which bin, in units per second.
The palette asks what color a speed is.  Written as one, every arithmetic case
had to be stated as a color tuple, so moving one gradient anchor by two units
turned nine of fourteen tests red and named the arithmetic in every failure.
The palette appears in `TestSpeedToColor` and nowhere else now.
"""
from __future__ import annotations

from player_core.funscript import Funscript

from main_player.heatmap import _speed_to_color, bin_speeds, build_heatmap


class TestSpeedToColor:
    def test_gradient_anchors(self):
        assert _speed_to_color(0) == (10, 14, 30)  # idle: near-black
        assert _speed_to_color(100) == (30, 70, 230)  # blue
        assert _speed_to_color(200) == (20, 210, 210)  # cyan
        assert _speed_to_color(300) == (40, 220, 50)  # green
        assert _speed_to_color(400) == (235, 220, 40)  # yellow
        assert _speed_to_color(500) == (240, 40, 30)  # red

    def test_interpolates_linearly_between_anchors(self):
        assert _speed_to_color(50) == (20, 42, 130)  # halfway near-black -> blue
        assert _speed_to_color(150) == (25, 140, 220)  # halfway blue -> cyan

    def test_clamps_above_top_anchor_to_red(self):
        assert _speed_to_color(501) == (240, 40, 30)
        assert _speed_to_color(10_000) == (240, 40, 30)


class TestTheStripIsThePaletteOverTheArithmetic:
    def test_each_bin_is_its_own_speed_as_a_color(self):
        """The only place the two halves meet, so the only test that needs a
        color at all."""
        fs = Funscript(actions=[(1000, 0), (1500, 100), (2000, 0)])

        colors = build_heatmap(fs, 4, start_ms=0, end_ms=4000)

        assert colors == [
            _speed_to_color(speed)
            for speed in bin_speeds(fs, 4, start_ms=0, end_ms=4000)
        ]

    def test_an_empty_window_draws_nothing(self):
        fs = Funscript(actions=[(0, 0), (1000, 100)])

        assert build_heatmap(fs, 10, start_ms=0, end_ms=0) == []


class TestHowMuchTravelLandsInEachBin:
    def test_an_empty_window_has_no_bins(self):
        fs = Funscript(actions=[(0, 0), (1000, 100)])

        assert bin_speeds(fs, 10, start_ms=0, end_ms=0) == []

    def test_a_script_with_nothing_to_move_between_is_idle_throughout(self):
        assert bin_speeds(Funscript(actions=[]), 4, start_ms=0, end_ms=10_000) == [0] * 4
        assert bin_speeds(
            Funscript(actions=[(500, 80)]), 4, start_ms=0, end_ms=10_000) == [0] * 4

    def test_full_travel_per_bin_reads_as_100_units_per_second(self):
        fs = Funscript(actions=[(0, 0), (1000, 100), (2000, 0), (3000, 100), (4000, 0)])

        assert bin_speeds(fs, 4, start_ms=0, end_ms=4000) == [100] * 4

    def test_a_segment_spanning_bins_splits_its_travel_by_overlap(self):
        # One 100-unit segment across the whole 2s: 50 units land in each 1s bin.
        fs = Funscript(actions=[(0, 0), (2000, 100)])

        assert bin_speeds(fs, 2, start_ms=0, end_ms=2000) == [50, 50]

    def test_bins_outside_the_scripted_range_are_idle(self):
        # Activity only in bin 1: bins before the first action and after the
        # last see nothing.
        fs = Funscript(actions=[(1000, 0), (1500, 100), (2000, 0)])

        assert bin_speeds(fs, 4, start_ms=0, end_ms=4000) == [0, 200, 0, 0]

    def test_a_zero_length_segment_is_ignored(self):
        # Duplicate timestamps happen in real scripts; an instantaneous jump has
        # no duration to average over and must not divide by zero.
        fs = Funscript(actions=[(0, 0), (1000, 100), (1000, 20), (2000, 120)])

        assert bin_speeds(fs, 2, start_ms=0, end_ms=2000) == [100, 100]

    def test_one_bin_averages_the_whole_video(self):
        # 100 units of travel in the first second, idle second second.
        fs = Funscript(actions=[(0, 0), (1000, 100)])

        assert bin_speeds(fs, 1, start_ms=0, end_ms=2000) == [50]

    def test_travel_past_the_video_end_does_not_count(self):
        # Half of the 100-unit segment happens after the video ends.
        fs = Funscript(actions=[(0, 0), (2000, 100)])

        assert bin_speeds(fs, 1, start_ms=0, end_ms=1000) == [50]


class TestAWindowInsideTheVideo:
    def test_its_bins_see_only_their_own_window(self):
        # Full-range segments at 100 units/s throughout.
        fs = Funscript(actions=[(0, 0), (1000, 100), (2000, 0), (3000, 100), (4000, 0)])

        assert bin_speeds(fs, 2, start_ms=1000, end_ms=3000) == [100, 100]

    def test_activity_entirely_before_it_is_excluded(self):
        fs = Funscript(actions=[(0, 0), (1000, 100)])

        assert bin_speeds(fs, 2, start_ms=2000, end_ms=4000) == [0, 0]

    def test_a_segment_straddling_its_start_counts_only_the_overlap(self):
        # The segment spans [500, 1500]; the window sees its second half.
        fs = Funscript(actions=[(500, 0), (1500, 100)])

        assert bin_speeds(fs, 1, start_ms=1000, end_ms=2000) == [50]
