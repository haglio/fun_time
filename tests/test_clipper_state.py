"""Tests for fun_time.robot_hand.clipper.state (pure logic, no real video files)."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from fun_time.robot_hand.clipper.state import (
    ExportJob,
    VideoState,
    accept_suggested_out,
    change_speed,
    contract_left,
    contract_right,
    cycle_loop_mode,
    current_loop_frame_index,
    extend_left,
    extend_right,
    index_for_timeline_x,
    loop_preview_indices,
    make_video_state,
    set_mark_in,
    set_mark_out,
    shift_active_range,
    timeline_x_for_index,
    toggle_loop_pause,
    update_loop_suggestions,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(
    *,
    total_frames: int = 100,
    loaded_start: int = 0,
    loaded_end: int | None = None,
    active_start: int = 10,
    active_end: int | None = None,
    current: int = 20,
    base_step: int = 5,
    fps: float = 30.0,
    speed: float = 1.0,
    wrap_mode: str = "blue",
    loop_mode: str = "base-tip-base",
    session_name: str = "test_session",
    initial_active_start: int | None = None,
    initial_active_end: int | None = None,
) -> VideoState:
    if loaded_end is None:
        loaded_end = total_frames - 1
    if active_end is None:
        active_end = total_frames - 10

    # Populate frames for the loaded range
    frames = {i: np.zeros((2, 2, 3), dtype=np.uint8) for i in range(loaded_start, loaded_end + 1)}

    cap = MagicMock()
    return VideoState(
        cap=cap,
        path="/fake/video.mp4",
        fps=fps,
        total_frames=total_frames,
        loaded_start=loaded_start,
        loaded_end=loaded_end,
        active_start=active_start,
        active_end=active_end,
        current=current,
        base_step=base_step,
        frames=frames,
        loop_anchor=time.monotonic(),
        session_name=session_name,
        session_path="/fake/sessions/test_session.json",
        original_session_payload={},
        loop_mode=loop_mode,
        speed=speed,
        wrap_mode=wrap_mode,
        initial_active_start=active_start if initial_active_start is None else initial_active_start,
        initial_active_end=active_end if initial_active_end is None else initial_active_end,
    )


def _pattern_frame(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(40, 40, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# ExportJob defaults
# ---------------------------------------------------------------------------

class TestExportJob:
    def test_default_not_active(self):
        j = ExportJob()
        assert j.active is False

    def test_default_not_done(self):
        j = ExportJob()
        assert j.done is False

    def test_default_not_failed(self):
        j = ExportJob()
        assert j.failed is False

    def test_default_not_dismissed(self):
        j = ExportJob()
        assert j.dismissed is False

    def test_default_stage_empty(self):
        j = ExportJob()
        assert j.stage == ""

    def test_default_progress_zero(self):
        j = ExportJob()
        assert j.clip_progress == 0.0
        assert j.fix_progress == 0.0
        assert j.audio_progress == 0.0

    def test_procs_list_is_fresh(self):
        j1, j2 = ExportJob(), ExportJob()
        assert j1.procs is not j2.procs


# ---------------------------------------------------------------------------
# VideoState computed properties
# ---------------------------------------------------------------------------

class TestVideoStateProperties:
    def test_active_count(self):
        s = _make_state(active_start=10, active_end=19)
        assert s.active_count == 10

    def test_loaded_count(self):
        s = _make_state(loaded_start=0, loaded_end=49)
        assert s.loaded_count == 50

    def test_active_count_single_frame(self):
        s = _make_state(active_start=5, active_end=5)
        assert s.active_count == 1

    def test_should_prompt_on_exit_only_for_existing_saved_data(self):
        s = _make_state()
        s.dirty = True
        assert s.should_prompt_on_exit is False

        s.protect_existing_save_data = True
        assert s.should_prompt_on_exit is True

    def test_should_not_prompt_when_clean_even_for_loaded_sessions(self):
        s = _make_state()
        s.protect_existing_save_data = True
        s.dirty = False
        assert s.should_prompt_on_exit is False


# ---------------------------------------------------------------------------
# clamp_current
# ---------------------------------------------------------------------------

class TestClampCurrent:
    def test_clamped_up_to_loaded_start_in_blue_mode(self):
        s = _make_state(loaded_start=10, current=5, wrap_mode="blue")
        s.clamp_current()
        assert s.current == s.loaded_start

    def test_clamped_down_to_loaded_end_in_blue_mode(self):
        s = _make_state(loaded_end=50, current=99, wrap_mode="blue")
        s.clamp_current()
        assert s.current == s.loaded_end

    def test_within_range_unchanged(self):
        s = _make_state(loaded_start=0, loaded_end=99, current=50, wrap_mode="blue")
        s.clamp_current()
        assert s.current == 50


# ---------------------------------------------------------------------------
# current_payload
# ---------------------------------------------------------------------------

class TestCurrentPayload:
    def test_has_required_keys(self):
        s = _make_state()
        payload = s.current_payload()
        for key in ("version", "session_name", "video_path", "fps", "total_frames",
                    "loaded_start", "loaded_end", "active_start", "active_end",
                    "current", "seconds_per_step", "loop_mode", "wrap_mode", "speed"):
            assert key in payload, f"Missing key: {key}"

    def test_version_is_1(self):
        s = _make_state()
        assert s.current_payload()["version"] == 1

    def test_session_name_correct(self):
        s = _make_state(session_name="my_clip")
        assert s.current_payload()["session_name"] == "my_clip"

    def test_seconds_per_step(self):
        s = _make_state(base_step=30, fps=30.0)
        assert s.current_payload()["seconds_per_step"] == pytest.approx(1.0)

    def test_wrap_mode_preserved(self):
        s = _make_state(wrap_mode="red")
        assert s.current_payload()["wrap_mode"] == "red"

    def test_loop_mode_preserved(self):
        s = _make_state(loop_mode="tip-base")
        assert s.current_payload()["loop_mode"] == "tip-base"


class TestCycleLoopMode:
    def test_cycles_to_next_mode(self):
        s = _make_state(loop_mode="base-tip-base")
        with patch.object(s, "mark_dirty"):
            cycle_loop_mode(s)
        assert s.loop_mode == "tip-base-tip"


class TestMakeVideoState:
    def test_new_session_keeps_requested_loop_mode(self):
        cap = MagicMock()
        cap.isOpened.return_value = True
        cap.get.side_effect = [30.0, 120.0]
        frames = {i: np.zeros((2, 2, 3), dtype=np.uint8) for i in range(30)}

        with patch("fun_time.robot_hand.clipper.state.cv2.VideoCapture", return_value=cap):
            with patch("fun_time.robot_hand.clipper.state.load_range", return_value=frames):
                state = make_video_state("/fake/video.mp4", "demo", 0.0, 1.0, loop_mode="tip-base")

        assert state.loop_mode == "tip-base"


class TestLoopPause:
    def test_current_loop_frame_stays_fixed_while_paused(self):
        s = _make_state(active_start=10, active_end=19, fps=10.0, speed=1.0)
        s.loop_anchor = 100.0
        with patch("fun_time.robot_hand.clipper.state.time.monotonic", side_effect=[100.45, 100.8]):
            toggle_loop_pause(s)
            first = current_loop_frame_index(s)
            second = current_loop_frame_index(s)
        assert s.loop_paused is True
        assert first == 14
        assert second == 14

    def test_toggle_pause_resume_keeps_same_frame_continuity(self):
        s = _make_state(active_start=10, active_end=19, fps=10.0, speed=1.0)
        s.loop_anchor = 100.0
        with patch("fun_time.robot_hand.clipper.state.time.monotonic", side_effect=[100.45, 100.45, 100.65]):
            toggle_loop_pause(s)
            toggle_loop_pause(s)
            resumed = current_loop_frame_index(s)
        assert s.loop_paused is False
        assert resumed == 16


class TestLoopPreviewIndices:
    def test_base_tip_preview_mirrors_back(self):
        s = _make_state(active_start=10, active_end=12, loop_mode="base-tip")
        assert loop_preview_indices(s) == [10, 11, 12, 11, 10]

    def test_tip_base_preview_prepends_reversed_half(self):
        s = _make_state(active_start=10, active_end=12, loop_mode="tip-base")
        assert loop_preview_indices(s) == [12, 11, 10, 11, 12]

    def test_tip_base_tip_preview_rotates_halfway(self):
        s = _make_state(active_start=10, active_end=15, loop_mode="tip-base-tip")
        assert loop_preview_indices(s) == [13, 14, 15, 10, 11, 12]


class TestChangeSpeed:
    def test_speed_does_not_drop_below_quarter_x(self):
        s = _make_state(speed=0.25)
        with patch("fun_time.robot_hand.clipper.state.time.monotonic", return_value=100.0):
            change_speed(s, -0.25)
        assert s.speed == pytest.approx(0.25)

    def test_change_speed_while_paused_keeps_paused_state(self):
        s = _make_state(active_start=10, active_end=19, fps=10.0, speed=1.0)
        s.loop_paused = True
        s.paused_loop_idx = 14
        with patch("fun_time.robot_hand.clipper.state.time.monotonic", return_value=100.0):
            change_speed(s, 0.25)
        assert s.loop_paused is True
        assert s.paused_loop_idx == 14
        assert s.speed == pytest.approx(1.25)


# ---------------------------------------------------------------------------
# contract_left / extend_left / contract_right / extend_right
# ---------------------------------------------------------------------------

class TestContractLeft:
    def test_shrinks_loaded_start_by_base_step(self):
        s = _make_state(loaded_start=0, active_start=20, base_step=5)
        # Need enough gap between loaded_start and active_start
        s.loaded_start = 10
        s.frames = {i: np.zeros((2, 2, 3), dtype=np.uint8) for i in range(10, 100)}
        with patch.object(s, "mark_dirty"):
            contract_left(s)
        assert s.loaded_start == 15

    def test_prunes_frames_and_signatures_before_new_loaded_start(self):
        s = _make_state(loaded_start=10, active_start=20, base_step=5)
        s.frames = {i: np.zeros((2, 2, 3), dtype=np.uint8) for i in range(10, 31)}
        s.frame_signatures = {i: np.zeros((2, 2), dtype=np.float32) for i in range(10, 31)}

        with patch.object(s, "mark_dirty"):
            contract_left(s)

        assert s.loaded_start == 15
        assert all(idx >= 15 for idx in s.frames)
        assert all(idx >= 15 for idx in s.frame_signatures)

    def test_does_nothing_when_gap_too_small(self):
        s = _make_state(loaded_start=0, active_start=3, base_step=5)
        s.frames = {i: np.zeros((2, 2, 3), dtype=np.uint8) for i in range(0, 100)}
        original = s.loaded_start
        with patch.object(s, "mark_dirty"):
            contract_left(s)
        assert s.loaded_start == original

    def test_current_clamped_upward(self):
        s = _make_state(loaded_start=0, active_start=20, base_step=5, current=3)
        s.loaded_start = 0
        s.frames = {i: np.zeros((2, 2, 3), dtype=np.uint8) for i in range(0, 100)}
        # Make gap > base_step
        s.active_start = 20
        with patch.object(s, "mark_dirty"):
            contract_left(s)
        assert s.current >= s.loaded_start


class TestContractRight:
    def test_shrinks_loaded_end(self):
        s = _make_state(loaded_end=99, active_end=70, base_step=5)
        with patch.object(s, "mark_dirty"):
            contract_right(s)
        assert s.loaded_end == 94

    def test_prunes_frames_and_signatures_after_new_loaded_end(self):
        s = _make_state(loaded_start=0, loaded_end=30, active_end=20, base_step=5)
        s.frames = {i: np.zeros((2, 2, 3), dtype=np.uint8) for i in range(0, 31)}
        s.frame_signatures = {i: np.zeros((2, 2), dtype=np.float32) for i in range(0, 31)}

        with patch.object(s, "mark_dirty"):
            contract_right(s)

        assert s.loaded_end == 25
        assert all(idx <= 25 for idx in s.frames)
        assert all(idx <= 25 for idx in s.frame_signatures)

    def test_does_nothing_when_gap_too_small(self):
        s = _make_state(loaded_end=99, active_end=97, base_step=5)
        original = s.loaded_end
        with patch.object(s, "mark_dirty"):
            contract_right(s)
        assert s.loaded_end == original


# ---------------------------------------------------------------------------
# set_mark_in / set_mark_out
# ---------------------------------------------------------------------------

class TestSetMarkIn:
    def test_advances_active_start_to_current(self):
        s = _make_state(active_start=5, active_end=50, current=20)
        with patch.object(s, "mark_dirty"), patch.object(s, "reset_loop_anchor"):
            set_mark_in(s)
        assert s.active_start == 20

    def test_does_not_advance_past_active_end(self):
        s = _make_state(active_start=5, active_end=50, current=55)
        original = s.active_start
        with patch.object(s, "mark_dirty"), patch.object(s, "reset_loop_anchor"):
            set_mark_in(s)
        # current > active_end, condition `current < active_end` is false → no change
        assert s.active_start == original


class TestSetMarkOut:
    def test_retreats_active_end_to_current(self):
        s = _make_state(active_start=5, active_end=50, current=30)
        with patch.object(s, "mark_dirty"), patch.object(s, "reset_loop_anchor"):
            set_mark_out(s)
        assert s.active_end == 30

    def test_does_not_retreat_before_active_start(self):
        s = _make_state(active_start=20, active_end=50, current=10)
        original = s.active_end
        with patch.object(s, "mark_dirty"), patch.object(s, "reset_loop_anchor"):
            set_mark_out(s)
        assert s.active_end == original


class TestShiftActiveRange:
    def test_shift_right_reuses_old_out_as_new_in(self):
        s = _make_state(active_start=10, active_end=20, current=14)
        with patch.object(s, "mark_dirty"), patch.object(s, "reset_loop_anchor"):
            shift_active_range(s, 1)
        assert s.active_start == 20
        assert s.active_end == 30
        assert s.current == 24

    def test_shift_left_reuses_old_in_as_new_out(self):
        s = _make_state(active_start=20, active_end=30, current=26)
        with patch.object(s, "mark_dirty"), patch.object(s, "reset_loop_anchor"):
            shift_active_range(s, -1)
        assert s.active_start == 10
        assert s.active_end == 20
        assert s.current == 16

    def test_shift_right_expands_loaded_bounds_when_needed(self):
        s = _make_state(loaded_start=0, loaded_end=24, active_start=10, active_end=20, current=12, total_frames=40)

        def fake_ensure_loaded(state: VideoState, want_start: int, want_end: int) -> None:
            state.loaded_start = min(state.loaded_start, want_start)
            state.loaded_end = max(state.loaded_end, want_end)

        with patch("fun_time.robot_hand.clipper.state.ensure_loaded", side_effect=fake_ensure_loaded):
            with patch("fun_time.robot_hand.clipper.state.update_loop_suggestions"):
                with patch.object(s, "mark_dirty"), patch.object(s, "reset_loop_anchor"):
                    shift_active_range(s, 1)
        assert s.loaded_end == 35
        assert s.active_start == 20
        assert s.active_end == 30

    def test_shift_left_expands_loaded_bounds_when_needed(self):
        s = _make_state(loaded_start=12, loaded_end=40, active_start=20, active_end=30, current=25, total_frames=60)

        def fake_ensure_loaded(state: VideoState, want_start: int, want_end: int) -> None:
            state.loaded_start = min(state.loaded_start, want_start)
            state.loaded_end = max(state.loaded_end, want_end)

        with patch("fun_time.robot_hand.clipper.state.ensure_loaded", side_effect=fake_ensure_loaded):
            with patch("fun_time.robot_hand.clipper.state.update_loop_suggestions"):
                with patch.object(s, "mark_dirty"), patch.object(s, "reset_loop_anchor"):
                    shift_active_range(s, -1)
        assert s.loaded_start == 5
        assert s.active_start == 10
        assert s.active_end == 20

    def test_shift_right_preserves_existing_loaded_end_when_buffer_already_exists(self):
        s = _make_state(loaded_start=0, loaded_end=40, active_start=10, active_end=20, current=14, base_step=5)
        with patch("fun_time.robot_hand.clipper.state.update_loop_suggestions"):
            with patch.object(s, "mark_dirty"), patch.object(s, "reset_loop_anchor"):
                shift_active_range(s, 1)
        assert s.loaded_end == 40
        assert s.active_end == 30

    def test_shift_left_preserves_existing_loaded_start_when_buffer_already_exists(self):
        s = _make_state(loaded_start=0, loaded_end=40, active_start=20, active_end=30, current=24, base_step=5)
        with patch("fun_time.robot_hand.clipper.state.update_loop_suggestions"):
            with patch.object(s, "mark_dirty"), patch.object(s, "reset_loop_anchor"):
                shift_active_range(s, -1)
        assert s.loaded_start == 0
        assert s.active_start == 10

    def test_shift_does_nothing_if_it_would_leave_video_bounds(self):
        s = _make_state(active_start=2, active_end=12, current=5)
        original = (s.active_start, s.active_end, s.current)
        with patch.object(s, "mark_dirty") as mark_dirty, patch.object(s, "reset_loop_anchor") as reset_anchor:
            shift_active_range(s, -1)
        assert (s.active_start, s.active_end, s.current) == original
        mark_dirty.assert_not_called()
        reset_anchor.assert_not_called()


class TestLoopSuggestions:
    def test_no_suggestions_for_untouched_initial_selection(self):
        s = _make_state(active_start=10, active_end=40, initial_active_start=10, initial_active_end=40)
        update_loop_suggestions(s)
        assert s.suggested_in is None
        assert s.suggested_out is None

    def test_marked_in_suggests_neighbor_before_matching_return_frame(self):
        s = _make_state(
            total_frames=80,
            loaded_start=0,
            loaded_end=79,
            active_start=10,
            active_end=60,
            current=10,
            initial_active_start=0,
            initial_active_end=60,
        )
        frames = {i: _pattern_frame(1000 + i) for i in range(80)}
        frames[10] = _pattern_frame(42)
        frames[12] = frames[10].copy()
        frames[50] = frames[10].copy()
        s.frames = frames

        update_loop_suggestions(s)

        assert s.suggested_in is None
        assert s.suggested_out == 49

    def test_when_both_marks_are_set_pair_can_nudge_to_better_neighboring_loop(self):
        s = _make_state(
            total_frames=80,
            loaded_start=0,
            loaded_end=79,
            active_start=10,
            active_end=20,
            current=10,
            initial_active_start=0,
            initial_active_end=79,
        )
        frames = {i: _pattern_frame(2000 + i) for i in range(80)}
        frames[10] = _pattern_frame(11)
        frames[11] = _pattern_frame(12)
        frames[21] = frames[10].copy()
        frames[22] = frames[11].copy()
        s.frames = frames

        update_loop_suggestions(s)

        assert s.suggested_in == 11
        assert s.suggested_out == 21

    def test_refinement_stays_anchored_after_accepting_suggested_out(self):
        s = _make_state(
            total_frames=80,
            loaded_start=0,
            loaded_end=79,
            active_start=10,
            active_end=20,
            current=10,
            initial_active_start=0,
            initial_active_end=79,
        )
        frames = {i: _pattern_frame(3000 + i) for i in range(80)}
        frames[10] = _pattern_frame(21)
        frames[11] = _pattern_frame(22)
        frames[12] = _pattern_frame(23)
        frames[21] = frames[10].copy()
        frames[22] = frames[11].copy()
        frames[23] = frames[12].copy()
        s.frames = frames
        s.suggested_out = 20
        s.suggestion_anchor_in = 10
        s.suggestion_anchor_out = 20

        accept_suggested_out(s)
        update_loop_suggestions(s)

        first_pair = (s.suggested_in, s.suggested_out)

        s.active_start = first_pair[0]
        s.active_end = first_pair[1]
        update_loop_suggestions(s)

        assert (s.suggested_in, s.suggested_out) == first_pair

    def test_base_tip_mode_uses_turning_point_for_suggested_out(self):
        s = _make_state(
            total_frames=80,
            loaded_start=0,
            loaded_end=79,
            active_start=10,
            active_end=60,
            initial_active_start=0,
            initial_active_end=60,
            loop_mode="base-tip",
        )
        with patch("fun_time.robot_hand.clipper.state._best_turning_point_index", return_value=37) as turning:
            with patch("fun_time.robot_hand.clipper.state._best_duplicate_match_index", return_value=49) as duplicate:
                update_loop_suggestions(s)
        assert s.suggested_out == 37
        turning.assert_called_once()
        duplicate.assert_not_called()

    def test_tip_base_mode_uses_turning_point_for_suggested_in(self):
        s = _make_state(
            total_frames=80,
            loaded_start=0,
            loaded_end=79,
            active_start=10,
            active_end=60,
            initial_active_start=10,
            initial_active_end=79,
            loop_mode="tip-base",
        )
        with patch("fun_time.robot_hand.clipper.state._best_turning_point_index", return_value=33) as turning:
            with patch("fun_time.robot_hand.clipper.state._pair_transition_score", return_value=999.0) as pair_score:
                update_loop_suggestions(s)
        assert s.suggested_in == 33
        turning.assert_called_once()
        pair_score.assert_not_called()

    def test_half_loop_modes_skip_pair_refinement_when_both_marks_changed(self):
        s = _make_state(
            total_frames=80,
            loaded_start=0,
            loaded_end=79,
            active_start=10,
            active_end=60,
            initial_active_start=0,
            initial_active_end=79,
            loop_mode="base-tip",
        )
        with patch("fun_time.robot_hand.clipper.state._best_turning_point_index", side_effect=[35, 24]) as turning:
            with patch("fun_time.robot_hand.clipper.state._pair_transition_score", return_value=999.0) as pair_score:
                update_loop_suggestions(s)
        assert s.suggested_out == 35
        assert s.suggested_in == 24
        assert turning.call_count == 2
        pair_score.assert_not_called()


# ---------------------------------------------------------------------------
# timeline_x_for_index / index_for_timeline_x
# ---------------------------------------------------------------------------

class TestTimelineXForIndex:
    def test_start_maps_to_x1(self):
        s = _make_state(loaded_start=0, loaded_end=99)
        assert timeline_x_for_index(s, 100, 900, 0) == 100

    def test_end_maps_to_x2(self):
        s = _make_state(loaded_start=0, loaded_end=99)
        assert timeline_x_for_index(s, 100, 900, 99) == 900

    def test_midpoint(self):
        s = _make_state(loaded_start=0, loaded_end=100)
        x = timeline_x_for_index(s, 0, 200, 50)
        assert x == pytest.approx(100, abs=2)


class TestIndexForTimelineX:
    def test_x1_gives_loaded_start(self):
        s = _make_state(loaded_start=0, loaded_end=99)
        assert index_for_timeline_x(s, 100, 900, 100) == 0

    def test_x2_gives_loaded_end(self):
        s = _make_state(loaded_start=0, loaded_end=99)
        assert index_for_timeline_x(s, 100, 900, 900) == 99

    def test_clamps_below_x1(self):
        s = _make_state(loaded_start=0, loaded_end=99)
        assert index_for_timeline_x(s, 100, 900, 0) == 0

    def test_clamps_above_x2(self):
        s = _make_state(loaded_start=0, loaded_end=99)
        assert index_for_timeline_x(s, 100, 900, 9999) == 99
