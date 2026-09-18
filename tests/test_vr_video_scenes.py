from __future__ import annotations

import pytest

from fun_time_vr.video_scenes import scene_starts_ms


def test_each_scene_starts_the_seconds_its_start_names_into_the_video():
    payload = {"scenes": [{"start": 0}, {"start": 95.5}, {"start": 1200}]}

    assert scene_starts_ms(payload) == (0.0, 95_500.0, 1_200_000.0)


def test_a_scene_record_with_no_readable_start_is_passed_over():
    payload = {"scenes": [{"start": 30}, {"name": "scene one"}, {"start": "soon"},
                          {"start": True}, {"start": -4}, "scene two"]}

    assert scene_starts_ms(payload) == (30_000.0,)


@pytest.mark.parametrize("scenes", [30, {"start": 30}, None])
def test_scenes_written_as_anything_but_a_list_are_no_scenes(scenes):
    assert scene_starts_ms({"scenes": scenes}) == ()


def test_the_scenes_come_back_in_the_order_they_play_and_once_each():
    payload = {"scenes": [{"start": 300}, {"start": 0}, {"start": 120}, {"start": 120.0}]}

    assert scene_starts_ms(payload) == (0.0, 120_000.0, 300_000.0)
