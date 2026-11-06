from __future__ import annotations

import shutil
from pathlib import Path

from funestra.video_groups import normalize_path_key

from fun_time.watch_stats import (
    WatchTracker,
    load_watch_stats,
    record_watch_event,
)


def _touch(tmp_path: Path, name: str) -> str:
    p = tmp_path / name
    p.write_text("x", encoding="utf-8")
    return str(p)


def test_record_watch_event_accumulates_counts_per_video(tmp_path: Path):
    stats_file = tmp_path / "state" / "watch_stats.json"
    video = _touch(tmp_path, "clip.mp4")

    record_watch_event(stats_file, video, "completion")
    record_watch_event(stats_file, video, "completion")
    record_watch_event(stats_file, video, "skip")
    record_watch_event(stats_file, video, "lock")

    stats = load_watch_stats(stats_file)
    entry = stats[normalize_path_key(video)]
    assert entry == {"completions": 2, "skips": 1, "locks": 1}


def test_watch_stats_that_are_missing_or_unreadable_count_as_none(tmp_path: Path):
    assert load_watch_stats(tmp_path / "absent.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert load_watch_stats(bad) == {}


def test_record_watch_event_prunes_entries_for_vanished_files(tmp_path: Path):
    stats_file = tmp_path / "watch_stats.json"
    keeper = _touch(tmp_path, "keeper.mp4")
    goner = _touch(tmp_path, "goner.mp4")
    record_watch_event(stats_file, keeper, "completion")
    record_watch_event(stats_file, goner, "skip")

    Path(goner).unlink()
    record_watch_event(stats_file, keeper, "completion")

    stats = load_watch_stats(stats_file)
    assert normalize_path_key(goner) not in stats
    assert stats[normalize_path_key(keeper)]["completions"] == 2


def test_record_watch_event_keeps_rows_whose_tree_is_out_of_reach(tmp_path: Path):
    stats_file = tmp_path / "watch_stats.json"
    volume = tmp_path / "volume"
    volume.mkdir()
    unmounted = _touch(volume, "clip.mp4")
    keeper = _touch(tmp_path, "keeper.mp4")
    record_watch_event(stats_file, unmounted, "completion")

    shutil.rmtree(volume)
    record_watch_event(stats_file, keeper, "completion")

    stats = load_watch_stats(stats_file)
    assert normalize_path_key(unmounted) in stats


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_tracker_counts_a_completion_when_a_watched_video_departs():
    clock = _Clock()
    tracker = WatchTracker(clock=clock)

    assert tracker.observe("a.mp4", 0.1) == []
    clock.now = 4.0
    assert tracker.observe("a.mp4", 0.9) == []
    clock.now = 8.0
    events = tracker.observe("b.mp4", 0.0)

    assert events == [("completion", "a.mp4")]


def test_tracker_counts_a_skip_when_user_navigates_away_early():
    clock = _Clock()
    tracker = WatchTracker(clock=clock)
    tracker.observe("a.mp4", 0.2)

    clock.now = 5.0
    tracker.note_user_nav()
    clock.now = 5.5
    events = tracker.observe("b.mp4", 0.0)

    assert events == [("skip", "a.mp4")]


def test_tracker_is_neutral_when_change_was_not_user_driven():
    clock = _Clock()
    tracker = WatchTracker(clock=clock)
    tracker.observe("a.mp4", 0.2)

    clock.now = 60.0
    assert tracker.observe("b.mp4", 0.0) == []


def test_tracker_is_neutral_when_user_leaves_late_but_unfinished():
    clock = _Clock()
    tracker = WatchTracker(clock=clock)
    tracker.observe("a.mp4", 0.7)

    tracker.note_user_nav()
    clock.now = 1.0
    assert tracker.observe("b.mp4", 0.0) == []


def test_tracker_counts_each_repeat_wrap_as_a_completion():
    clock = _Clock()
    tracker = WatchTracker(clock=clock)
    tracker.observe("a.mp4", 0.3)
    tracker.observe("a.mp4", 0.95)

    first_wrap = tracker.observe("a.mp4", 0.05)
    tracker.observe("a.mp4", 0.9)
    second_wrap = tracker.observe("a.mp4", 0.02)
    tracker.observe("a.mp4", 0.97)
    departed = tracker.observe("b.mp4", 0.0)

    assert first_wrap == [("completion", "a.mp4")]
    assert second_wrap == [("completion", "a.mp4")]
    assert departed == [("completion", "a.mp4")]


def test_tracker_discard_suppresses_classifying_the_departed_video():
    clock = _Clock()
    tracker = WatchTracker(clock=clock)
    tracker.observe("a.mp4", 0.9)

    tracker.note_discard()
    assert tracker.observe("b.mp4", 0.0) == []

    tracker.observe("b.mp4", 0.95)
    assert tracker.observe("c.mp4", 0.0) == [("completion", "b.mp4")]


class TestTheShapeAnotherAppRewrites:
    def test_a_row_is_keyed_by_the_path_as_this_app_normalizes_it(self, tmp_path: Path):
        video = tmp_path / "Portrait" / "Example Clip.mp4"
        video.parent.mkdir(parents=True)
        video.write_text("v", encoding="utf-8")
        stats_file = tmp_path / "watch_stats.json"

        record_watch_event(stats_file, str(video), "completion")

        assert list(load_watch_stats(stats_file)) == [normalize_path_key(str(video))]

    def test_a_row_holds_the_three_counts_and_nothing_else(self, tmp_path: Path):
        video = tmp_path / "clip.mp4"
        video.write_text("v", encoding="utf-8")
        stats_file = tmp_path / "watch_stats.json"

        record_watch_event(stats_file, str(video), "skip")

        row = load_watch_stats(stats_file)[normalize_path_key(str(video))]
        assert sorted(row) == ["completions", "locks", "skips"]
