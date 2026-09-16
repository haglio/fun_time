from __future__ import annotations

from pathlib import Path

from main_player.play_points import REMEMBERED, PlayPoints

VIDEO = Path("C:/library/feature.mp4")
OTHER = Path("C:/library/another.mp4")
HOUR_MS = 3_600_000


def _watch(points, video, position_ms, duration_ms=HOUR_MS):
    """Two ticks at *position_ms*: the jump onto it, then playing on from it."""
    points.observe(video, position_ms, duration_ms)
    points.observe(video, position_ms, duration_ms)


def test_a_video_left_in_the_middle_comes_back_where_it_was(tmp_path):
    file = tmp_path / "points.json"
    _watch(PlayPoints(file), VIDEO, 300_000)
    assert PlayPoints(file).point_for(VIDEO) == 300_000


def test_a_video_only_just_started_has_nothing_to_come_back_to(tmp_path):
    file = tmp_path / "points.json"
    _watch(PlayPoints(file), VIDEO, 20_000)
    assert PlayPoints(file).point_for(VIDEO) == 0


def test_a_video_played_to_the_end_starts_over_next_time(tmp_path):
    file = tmp_path / "points.json"
    points = PlayPoints(file)
    _watch(points, VIDEO, 300_000)
    _watch(points, VIDEO, HOUR_MS - 5_000)
    assert PlayPoints(file).point_for(VIDEO) == 0


def test_a_player_that_has_not_said_how_long_the_video_is_forgets_nothing(tmp_path):
    file = tmp_path / "points.json"
    points = PlayPoints(file)
    _watch(points, VIDEO, 300_000)
    _watch(points, VIDEO, 300_000, duration_ms=0)
    assert PlayPoints(file).point_for(VIDEO) == 300_000


def test_a_resumed_video_starts_a_moment_before_it_was_left(tmp_path):
    file = tmp_path / "points.json"
    _watch(PlayPoints(file), VIDEO, 304_200)
    assert PlayPoints(file).point_for(VIDEO) == 300_000


def test_the_file_is_left_alone_while_the_point_stands(tmp_path):
    file = tmp_path / "points.json"
    points = PlayPoints(file)
    _watch(points, VIDEO, 300_000)
    file.unlink()
    _watch(points, VIDEO, 304_000)
    assert not file.exists()


def test_a_clock_that_did_not_play_its_way_there_is_not_written_down(tmp_path):
    file = tmp_path / "points.json"
    points = PlayPoints(file)
    points.observe(VIDEO, position_ms=0, duration_ms=HOUR_MS)
    points.observe(VIDEO, position_ms=300_000, duration_ms=HOUR_MS)
    assert PlayPoints(file).point_for(VIDEO) == 0


def test_the_first_tick_on_a_video_is_not_written_down(tmp_path):
    file = tmp_path / "points.json"
    points = PlayPoints(file)
    _watch(points, OTHER, 300_000)
    points.observe(VIDEO, position_ms=300_000, duration_ms=HOUR_MS)
    assert PlayPoints(file).point_for(VIDEO) == 0


def _fill(points, count):
    for n in range(count):
        _watch(points, Path(f"C:/library/{n}.mp4"), 300_000)


def test_only_the_videos_watched_most_recently_are_remembered(tmp_path):
    file = tmp_path / "points.json"
    _fill(PlayPoints(file), REMEMBERED + 1)
    reread = PlayPoints(file)
    assert reread.point_for(Path("C:/library/0.mp4")) == 0
    assert reread.point_for(Path(f"C:/library/{REMEMBERED}.mp4")) == 300_000


def test_a_video_still_being_watched_is_not_the_one_forgotten(tmp_path):
    file = tmp_path / "points.json"
    points = PlayPoints(file)
    _fill(points, REMEMBERED)
    _watch(points, Path("C:/library/0.mp4"), 600_000)
    _watch(points, Path("C:/library/new.mp4"), 300_000)
    assert PlayPoints(file).point_for(Path("C:/library/0.mp4")) == 600_000


def test_a_video_spelled_another_way_is_the_same_video(tmp_path):
    file = tmp_path / "points.json"
    _watch(PlayPoints(file), Path("C:/Library/Feature.mp4"), 300_000)
    assert PlayPoints(file).point_for("c:/library/feature.mp4") == 300_000


def test_a_file_holding_something_else_starts_the_videos_over(tmp_path):
    file = tmp_path / "points.json"
    file.write_text("[1, 2, 3]")
    assert PlayPoints(file).point_for(VIDEO) == 0
