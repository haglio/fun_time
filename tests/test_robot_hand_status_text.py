from __future__ import annotations

from fun_time.robot_hand.status_text import (
    active_clip_status_text,
    exception_status_text,
    listener_error_status_text,
    loading_status_text,
)


def test_exception_status_text_mentions_log_file():
    assert exception_status_text("boom", log_name="robot_hand_listener.log") == "Error: boom\nSee robot_hand_listener.log"


def test_listener_error_status_text_uses_multiline_error_prefix():
    assert listener_error_status_text("udp failed") == "Error:\nudp failed"


def test_loading_status_text_contains_basic_runtime_fields():
    text = loading_status_text(
        clip_name="demo.mp4",
        clip_index=2,
        clip_count=5,
        loading=True,
    )

    assert "clip=demo.mp4" in text
    assert "clip_index=2/5" in text
    assert "loading=True" in text
    assert "keys=[ and ] switch clips" in text


def test_active_clip_status_text_formats_phase_and_estimated_bpm():
    text = active_clip_status_text(
        clip_name="demo.mp4",
        clip_index=2,
        clip_count=5,
        frame_index=7,
        frame_count=12,
        visible=True,
        auto_active=False,
        phase=0.1254,
        raw_bpm=120.0,
        estimated_bpm=119.876,
        beats=4,
        loop_duration=2.0,
        stroke_name="pull",
        pattern_duration=1.5,
        loading=False,
        last_msg="AUTO 0",
    )

    assert "frame=7/12" in text
    assert "state=auto-off" in text
    assert "phase=0.125" in text
    assert "est_bpm=119.88" in text
    assert "last_msg=AUTO 0" in text


def test_active_clip_status_text_uses_na_for_missing_estimated_bpm():
    text = active_clip_status_text(
        clip_name="demo.mp4",
        clip_index=1,
        clip_count=1,
        frame_index=1,
        frame_count=1,
        visible=False,
        auto_active=True,
        phase=0.0,
        raw_bpm=None,
        estimated_bpm=None,
        beats=None,
        loop_duration=None,
        stroke_name="",
        pattern_duration=None,
        loading=True,
        last_msg="",
    )

    assert "state=auto-on" in text
    assert "est_bpm=n/a" in text
    assert "loading=True" in text
