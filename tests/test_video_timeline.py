"""Unit tests for the per-player video timeline."""
from __future__ import annotations

from fun_time.video_timeline import VideoTimeline


class TestPathAt:
    def test_returns_the_only_observed_video(self):
        timeline = VideoTimeline()
        timeline.observe("a.mp4", now=10.0)
        assert timeline.path_at(10.5) == "a.mp4"

    def test_a_transition_is_dated_to_the_midpoint_between_samples(self):
        """Sampling only bounds a switch to the gap between two samples, so the
        timeline splits the difference — error is symmetric, not one-sided."""
        timeline = VideoTimeline()
        timeline.observe("a.mp4", now=10.0)
        timeline.observe("b.mp4", now=11.0)
        assert timeline.path_at(10.4) == "a.mp4"
        assert timeline.path_at(10.6) == "b.mp4"

    def test_forgets_videos_older_than_the_retention_window(self):
        """Only the recent past is kept — enough to cover any utterance — and
        the video straddling the window's start survives so it stays queryable."""
        timeline = VideoTimeline(history_s=5.0)
        timeline.observe("a.mp4", now=0.0)
        timeline.observe("b.mp4", now=1.0)   # b starts at 0.5
        timeline.observe("c.mp4", now=10.0)  # c starts at 5.5; cutoff is 5.0
        assert timeline.path_at(0.2) == ""       # 'a' has been forgotten
        assert timeline.path_at(5.2) == "b.mp4"  # 'b' still covers the window

    def test_a_failed_sample_is_not_a_transition(self):
        """A status file carries no path before its player has loaded anything —
        a gap in the record, not a video change."""
        timeline = VideoTimeline()
        timeline.observe("a.mp4", now=10.0)
        timeline.observe("", now=11.0)
        assert timeline.path_at(11.5) == "a.mp4"
