from __future__ import annotations

import numpy as np
import pytest
from player_core.funscript import Funscript
from player_core.modes import LoopState
from player_core.timeline import BAR_INSET_Y, bar_track_x, progress_bar_bgra

from main_player.heatmap import build_heatmap
from main_player.overlay import (
    TIMELINE_HEIGHT,
    HeatmapStrip,
    LoopThumbCapture,
    ZoomWindow,
    heatmap_bgra,
    label_xs,
    loop_thumbnail_xys,
    time_to_x,
    timeline_bgra,
    timeline_height,
    timeline_x,
)


def _funscript():
    return Funscript(actions=[(0, 0), (1000, 100), (2000, 0)])


WIN_W = 1000
TRACK_W = bar_track_x(WIN_W)[1] - bar_track_x(WIN_W)[0]


class TestTheHeightOfTheTimelineRow:
    """How tall the lower edge of the window is, whatever is drawn there.

    Every video has a clickable timeline — the heatmap where there is a
    funscript, a plain progress bar where there is not — so the row is never
    absent, and the two things measured against it (where a press lands, where
    the volume chip sits) can ask one question instead of two.
    """

    def test_a_scripted_video_is_measured_by_its_strip(self):
        strip = HeatmapStrip()
        strip.update("v0.mp4", _funscript(), 4000.0, width=40)

        assert timeline_height(strip) == strip.height == 24

    def test_an_unscripted_video_still_leaves_the_row_the_bar_needs(self):
        """The strip is 0 there, and a row of no height would put the whole
        timeline outside the window and the chip on its lower edge."""
        strip = HeatmapStrip()
        strip.update("plain.mp4", None, 4000.0, width=40)

        assert strip.height == 0
        assert timeline_height(strip) == TIMELINE_HEIGHT

    def test_a_strip_that_grew_to_record_takes_the_row_with_it(self):
        strip = HeatmapStrip()
        strip.update("v0.mp4", _funscript(), 4000.0, width=40,
                     loop_state=LoopState.RECORDING, record_in_ms=1000.0, position_ms=1200.0)

        assert timeline_height(strip) == 48


class TestHeatmapStrip:
    def test_builds_one_color_per_pixel_of_the_track_it_fills(self):
        fs = _funscript()
        strip = HeatmapStrip()

        strip.update("v0.mp4", fs, 4000.0, width=WIN_W)

        assert strip.colors == build_heatmap(fs, TRACK_W, start_ms=0, end_ms=4000.0)
        assert strip.height == 24

    def test_unscripted_video_has_no_strip(self):
        strip = HeatmapStrip()

        strip.update("plain.mp4", None, 4000.0, width=WIN_W)

        assert strip.colors == []
        assert strip.height == 0

    def test_caches_until_the_video_changes(self):
        strip = HeatmapStrip()
        strip.update("v0.mp4", _funscript(), 4000.0, width=WIN_W)
        built = strip.colors

        # Same video key: not rebuilt even if a different funscript is passed.
        strip.update("v0.mp4", Funscript(actions=[]), 4000.0, width=WIN_W)
        assert strip.colors is built

        # New video key: rebuilt.
        strip.update("v1.mp4", Funscript(actions=[]), 4000.0, width=WIN_W)
        assert strip.colors != built

    def test_width_change_rebuilds(self):
        strip = HeatmapStrip()
        strip.update("v0.mp4", _funscript(), 4000.0, width=WIN_W)

        strip.update("v0.mp4", _funscript(), 4000.0, width=800)

        assert len(strip.colors) == bar_track_x(800)[1] - bar_track_x(800)[0]

    def test_full_view_window_spans_the_video(self):
        strip = HeatmapStrip()

        strip.update("v0.mp4", _funscript(), 4000.0, width=WIN_W)

        assert strip.window == (0.0, 4000.0)
        assert strip.record_in_ms is None

    def test_recording_zooms_into_a_taller_strip_around_the_in_point(self):
        fs = _funscript()
        strip = HeatmapStrip()

        strip.update(
            "v0.mp4", fs, 600_000.0, width=WIN_W,
            loop_state=LoopState.RECORDING, record_in_ms=50_000, position_ms=50_000.0,
        )

        assert strip.window == (48_000, 70_000)
        assert strip.height == 48
        assert strip.record_in_ms == 50_000
        assert strip.colors == build_heatmap(fs, TRACK_W, start_ms=48_000, end_ms=70_000)

    def test_recording_view_rescales_in_steps_not_continuously(self):
        strip = HeatmapStrip()

        def update(position_ms):
            strip.update(
                "v0.mp4", _funscript(), 600_000.0, width=WIN_W,
                loop_state=LoopState.RECORDING, record_in_ms=50_000, position_ms=position_ms,
            )

        update(50_000.0)
        held = strip.colors
        update(60_000.0)  # inside 85%: window and colors untouched
        assert strip.window == (48_000, 70_000)
        assert strip.colors is held

        update(66_800.0)  # past 85%: window doubles, colors rebuilt
        assert strip.window == (46_000, 90_000)
        assert strip.colors is not held

    def test_leaving_recording_restores_the_full_view(self):
        fs = _funscript()
        strip = HeatmapStrip()
        strip.update(
            "v0.mp4", fs, 600_000.0, width=WIN_W,
            loop_state=LoopState.RECORDING, record_in_ms=50_000, position_ms=66_800.0,
        )

        strip.update("v0.mp4", fs, 600_000.0, width=WIN_W, loop_state=LoopState.LOOPING)

        assert strip.window == (0.0, 600_000.0)
        assert strip.height == 24
        assert strip.record_in_ms is None
        assert strip.colors == build_heatmap(fs, TRACK_W, start_ms=0, end_ms=600_000.0)

        # A later recording zooms afresh from its own in point.
        strip.update(
            "v0.mp4", fs, 600_000.0, width=WIN_W,
            loop_state=LoopState.RECORDING, record_in_ms=100_000, position_ms=100_000.0,
        )
        assert strip.window == (98_000, 120_000)


class TestTimeToX:
    def test_maps_window_fraction_to_pixel(self):
        assert time_to_x(0, 0, 10_000, 100) == 0
        assert time_to_x(5_000, 0, 10_000, 100) == 50
        assert time_to_x(30_000, 20_000, 40_000, 100) == 50

    def test_clamps_to_the_strip(self):
        assert time_to_x(10_000, 0, 10_000, 100) == 99  # window end stays visible
        assert time_to_x(50_000, 20_000, 40_000, 100) == 99
        assert time_to_x(0, 20_000, 40_000, 100) == 0

    def test_empty_window_pins_left(self):
        assert time_to_x(500, 0, 0, 100) == 0


class TestZoomWindow:
    def test_initial_window_spans_20s_with_a_10_percent_lead(self):
        zoom = ZoomWindow(in_ms=50_000)

        assert zoom.bounds == (48_000, 70_000)  # 2s lead before the in point

    def test_holds_while_playhead_is_before_85_percent(self):
        zoom = ZoomWindow(in_ms=50_000)

        zoom.update(66_000)  # grow point is 48_000 + 0.85 * 22_000 = 66_700

        assert zoom.bounds == (48_000, 70_000)

    def test_doubles_span_once_playhead_passes_85_percent(self):
        zoom = ZoomWindow(in_ms=50_000)

        zoom.update(66_800)

        assert zoom.bounds == (46_000, 90_000)  # span 40s, lead 4s

    def test_far_jump_catches_up_in_doubling_steps(self):
        zoom = ZoomWindow(in_ms=50_000)

        zoom.update(200_000)  # e.g. a seek while recording

        assert zoom.bounds == (18_000, 370_000)  # span doubled 20s -> 320s



def _rgba(bar, y, x):
    px = bar[y, x]
    return int(px[2]), int(px[1]), int(px[0]), int(px[3])


# The plain scrubber and its track geometry moved to player_core.timeline —
# tested there (tests/test_timeline.py).  What stays here is the main player's own funscript
# heatmap, which builds on that shared frame.


class TestHeatmapBgra:
    def _framed_strip(self, win_w=1000):
        x0, x1 = bar_track_x(win_w)
        strip = HeatmapStrip()
        strip.update("v.mp4", _funscript(), 4000.0, width=win_w)
        return heatmap_bgra(strip, 2000, (1000, 3000), win_w), x0, x1

    def test_strip_is_inset_from_the_window_edges(self):
        bgra, x0, x1 = self._framed_strip()
        my = bgra.shape[0] // 2
        assert bgra[my, 5, 3] == 0 and bgra[my, bgra.shape[1] - 5, 3] == 0  # nothing at the edges
        assert bgra[my, (x0 + x1) // 2, 3] > 0                 # painted in the track

    def test_has_a_two_tone_border(self):
        bgra, x0, x1 = self._framed_strip()
        outer = _rgba(bgra, BAR_INSET_Y, x0 + 10)      # dark outer edge (away from marks)
        inner = _rgba(bgra, BAR_INSET_Y + 1, x0 + 10)  # light inner border
        assert max(outer[:3]) < 100
        assert min(inner[:3]) >= 200 and inner[3] >= 200

    def test_prominent_full_height_marks(self):
        bgra, x0, x1 = self._framed_strip()
        my = bgra.shape[0] // 2
        cx = x0 + (x1 - x0) // 2
        assert _rgba(bgra, my, cx) == (255, 255, 255, 255)     # white playcursor @50%
        white = [x for x in range(x0, x1) if _rgba(bgra, my, x) == (255, 255, 255, 255)]
        assert len(white) >= 3                                 # 3px, not a hairline
        amber = [x for x in range(x0, x1) if _rgba(bgra, my, x)[:3] == (235, 180, 60)]
        assert amber and any(x < cx for x in amber) and any(x > cx for x in amber)

    def test_unscripted_strip_is_none(self):
        strip = HeatmapStrip()
        strip.update("plain.mp4", None, 4000.0, width=100)
        assert heatmap_bgra(strip, 0, None, 100) is None


class TestTheTimelineUnderAVideo:
    def test_an_unscripted_video_gets_the_plain_bar_across_its_whole_length(self):
        strip = HeatmapStrip()
        strip.update("plain.mp4", None, 4000.0, width=WIN_W)

        drawn = timeline_bgra(strip, 2000, (1000, 3000), WIN_W, record_in_ms=500)

        assert np.array_equal(
            drawn, progress_bar_bgra(2000, 4000.0, (1000, 3000), WIN_W, record_in_ms=500))

    def test_a_scripted_video_gets_its_heatmap_strip_instead(self):
        strip = HeatmapStrip()
        strip.update("v0.mp4", _funscript(), 4000.0, width=WIN_W)

        drawn = timeline_bgra(strip, 2000, (1000, 3000), WIN_W)

        assert np.array_equal(drawn, heatmap_bgra(strip, 2000, (1000, 3000), WIN_W))

    @pytest.mark.parametrize("funscript", [None, _funscript()], ids=["plain", "scripted"])
    def test_it_says_where_it_draws_the_playcursor(self, funscript):
        strip = HeatmapStrip()
        strip.update("v0.mp4", funscript, 4000.0, width=WIN_W)

        for position in range(100, 3900, 7):
            bar = timeline_bgra(strip, position, None, WIN_W)
            white = np.flatnonzero((bar[bar.shape[0] // 2] == 255).all(axis=1))
            x = timeline_x(strip, position, WIN_W)
            assert white.tolist() == [x - 1, x, x + 1]


class TestLoopThumbCapture:
    def test_asks_for_in_first_then_out_near_end(self):
        cap = LoopThumbCapture()

        assert cap.needed(LoopState.LOOPING, (2000, 4000), 2000) == "in"
        cap.set("in", object())
        assert cap.needed(LoopState.LOOPING, (2000, 4000), 2500) is None  # not near out yet
        assert cap.needed(LoopState.LOOPING, (2000, 4000), 3700) == "out"  # within 400ms of 4000
        cap.set("out", object())
        assert cap.needed(LoopState.LOOPING, (2000, 4000), 3900) is None

    def test_clears_when_loop_ends(self):
        cap = LoopThumbCapture()
        cap.needed(LoopState.LOOPING, (2000, 4000), 2000)
        cap.set("in", object())

        assert cap.needed(LoopState.NORMAL, None, 0) is None
        assert cap.in_thumb is None

    def test_reset_thumbs_on_new_loop_bounds(self):
        cap = LoopThumbCapture()
        cap.needed(LoopState.LOOPING, (2000, 4000), 2000)
        cap.set("in", object())

        # a different loop → in_thumb must be re-requested
        assert cap.needed(LoopState.LOOPING, (5000, 7000), 5000) == "in"


class TestWhereTheLoopsTwoFramesGo:
    """Above their own marks on the timeline's inset track, and clear of the
    row itself -- which is the heatmap strip where there is one and the plain
    bar's own height where there is not, so the frames never sit on top of the
    thing they are labelling.

    The numbers are written out rather than recomputed: this is where the
    thumbnails ended up on a 1000x600 window, and a change to any of the three
    helpers underneath (the track inset, the time-to-pixel mapping, the
    overlap nudge) moves them.
    """

    TRACK = (40, 868)
    WIN_W, WIN_H = 1000, 600
    FRAME_H, FRAME_W = 10, 20

    def _thumbs(self, *, out=True):
        thumbs = LoopThumbCapture()
        thumbs.set("in", np.zeros((self.FRAME_H, self.FRAME_W, 4), dtype=np.uint8))
        if out:
            thumbs.set("out", np.zeros((self.FRAME_H, self.FRAME_W, 4), dtype=np.uint8))
        return thumbs

    def _xys(self, heatmap, thumbs, bounds):
        return loop_thumbnail_xys(heatmap, thumbs, bounds, track=self.TRACK,
                                  win_w=self.WIN_W, win_h=self.WIN_H)

    def _scripted(self) -> HeatmapStrip:
        strip = HeatmapStrip()
        strip.update("v0.mp4", _funscript(), 4000.0, width=40)
        return strip

    def test_each_frame_sits_centered_above_its_own_mark(self):
        assert self._xys(self._scripted(), self._thumbs(), (2000, 3000)) == (
            (444, 564), (651, 564))

    def test_an_unscripted_video_still_clears_the_row_its_bar_needs(self):
        """No strip is 0 tall, and frames measured against that would sit on
        the bar rather than above it."""
        strip = HeatmapStrip()
        strip.update("plain.mp4", None, 4000.0, width=40)

        assert self._xys(strip, self._thumbs(), (2000, 3000))[0] == (444, 564)

    def test_a_frame_not_grabbed_yet_has_nowhere_to_go(self):
        in_at, out_at = self._xys(self._scripted(), self._thumbs(out=False),
                                  (2000, 3000))

        assert (in_at, out_at) == ((444, 564), None)

    def test_two_marks_too_close_together_push_their_frames_apart(self):
        """Centered on both, the frames would overlap and the second would be
        unreadable; the out frame steps to the right of the in frame instead."""
        (in_x, _y), (out_x, _oy) = self._xys(self._scripted(), self._thumbs(),
                                             (2000, 2050))

        assert out_x >= in_x + self.FRAME_W


class TestWhereTheLoopsTwoLabelsGo:
    def test_centers_and_avoids_overlap(self):
        assert label_xs(100, 500, 60, 60, 1000) == (70, 470)
        ix, ox = label_xs(100, 120, 60, 60, 1000)  # markers close
        assert ox >= ix + 60
