from __future__ import annotations

import json
from pathlib import Path

from main_player.play_points import REMEMBERED, WRITE_EVERY_S, PlayPoints

VIDEO = Path("C:/library/feature.mp4")
OTHER = Path("C:/library/another.mp4")
HOUR_MS = 3_600_000


class _Clock:
    """A clock that only moves when a test moves it."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _points(file) -> PlayPoints:
    return PlayPoints(file, clock=_Clock())


def _watch(points, video, position_ms, duration_ms=HOUR_MS):
    """Two ticks at *position_ms*: the jump onto it, then playing on from it."""
    points.observe(video, position_ms, duration_ms)
    points.observe(video, position_ms, duration_ms)


def test_a_video_left_in_the_middle_comes_back_where_it_was(tmp_path):
    file = tmp_path / "points.json"
    _watch(_points(file), VIDEO, 300_000)
    assert PlayPoints(file).point_for(VIDEO) == 300_000


def test_a_video_left_seconds_in_comes_back_there_too(tmp_path):
    file = tmp_path / "points.json"
    _watch(_points(file), VIDEO, 3_000)
    assert PlayPoints(file).point_for(VIDEO) == 3_000


def test_a_short_clip_remembers_its_own_seconds(tmp_path):
    file = tmp_path / "points.json"
    _watch(_points(file), VIDEO, 4_200, duration_ms=8_000)
    assert PlayPoints(file).point_for(VIDEO) == 4_200


def test_a_video_played_to_its_end_comes_back_at_its_end(tmp_path):
    file = tmp_path / "points.json"
    _watch(_points(file), VIDEO, HOUR_MS - 40)
    assert PlayPoints(file).point_for(VIDEO) == HOUR_MS - 40


def test_leaving_a_video_writes_down_the_very_spot(tmp_path):
    file = tmp_path / "points.json"
    points = _points(file)
    _watch(points, VIDEO, 300_000)
    for ms in (300_016, 300_032, 300_048):
        points.observe(VIDEO, ms, HOUR_MS)

    points.leave()

    assert PlayPoints(file).point_for(VIDEO) == 300_048


def test_a_video_back_at_its_top_is_remembered_no_more(tmp_path):
    file = tmp_path / "points.json"
    points = _points(file)
    _watch(points, VIDEO, 300_000)
    points.observe(VIDEO, 0, HOUR_MS)

    points.leave()

    assert PlayPoints(file).point_for(VIDEO) == 0


def test_a_player_that_has_not_said_how_long_the_video_is_forgets_nothing(tmp_path):
    file = tmp_path / "points.json"
    points = _points(file)
    _watch(points, VIDEO, 300_000)
    _watch(points, VIDEO, 0, duration_ms=0)

    points.leave()

    assert PlayPoints(file).point_for(VIDEO) == 300_000


def test_a_clock_that_did_not_play_its_way_there_is_not_written_down(tmp_path):
    file = tmp_path / "points.json"
    points = _points(file)
    points.observe(VIDEO, 0, HOUR_MS)
    points.observe(VIDEO, 300_000, HOUR_MS)
    assert PlayPoints(file).point_for(VIDEO) == 0


def test_the_video_just_left_is_not_written_down_against_the_one_just_opened(tmp_path):
    file = tmp_path / "points.json"
    points = _points(file)
    _watch(points, OTHER, 300_000)

    points.observe(VIDEO, 300_000, HOUR_MS)

    assert PlayPoints(file).point_for(VIDEO) == 0


def test_the_file_is_not_rewritten_every_tick(tmp_path):
    file = tmp_path / "points.json"
    points = _points(file)
    _watch(points, VIDEO, 300_000)
    file.unlink()

    for ms in (300_016, 300_032, 300_048):
        points.observe(VIDEO, ms, HOUR_MS)

    assert not file.exists()


def test_a_session_that_is_killed_still_knows_roughly_where_it_was(tmp_path):
    file = tmp_path / "points.json"
    clock = _Clock()
    points = PlayPoints(file, clock=clock)
    _watch(points, VIDEO, 300_000)
    clock.now += WRITE_EVERY_S

    points.observe(VIDEO, 300_016, HOUR_MS)

    assert PlayPoints(file).point_for(VIDEO) == 300_016


def _seed(file, count):
    file.write_text(json.dumps(
        {str(Path(f"C:/library/{n}.mp4")).lower(): 300_000 for n in range(count)}))


def test_only_the_videos_watched_most_recently_are_remembered(tmp_path):
    file = tmp_path / "points.json"
    _seed(file, REMEMBERED)
    _watch(_points(file), Path("C:/library/new.mp4"), 300_000)

    reread = PlayPoints(file)
    assert reread.point_for(Path("C:/library/0.mp4")) == 0
    assert reread.point_for(Path("C:/library/new.mp4")) == 300_000


def test_a_video_still_being_watched_is_not_the_one_forgotten(tmp_path):
    file = tmp_path / "points.json"
    _seed(file, REMEMBERED)
    points = _points(file)
    _watch(points, Path("C:/library/0.mp4"), 600_000)
    _watch(points, Path("C:/library/new.mp4"), 300_000)

    assert PlayPoints(file).point_for(Path("C:/library/0.mp4")) == 600_000


def test_a_video_spelled_another_way_is_the_same_video(tmp_path):
    file = tmp_path / "points.json"
    _watch(_points(file), Path("C:/Library/Feature.mp4"), 300_000)
    assert PlayPoints(file).point_for("c:/library/feature.mp4") == 300_000


def test_a_file_holding_something_else_starts_the_videos_over(tmp_path):
    file = tmp_path / "points.json"
    file.write_text("[1, 2, 3]")
    assert PlayPoints(file).point_for(VIDEO) == 0


def test_a_video_that_ran_out_has_no_point_to_come_back_to(tmp_path):
    file = tmp_path / "points.json"
    points = _points(file)
    _watch(points, VIDEO, HOUR_MS - 40)

    points.ended()

    assert PlayPoints(file).point_for(VIDEO) == 0
