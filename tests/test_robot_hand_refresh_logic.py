from __future__ import annotations

from fun_time.robot_hand.refresh_logic import (
    SharedStateSnapshot,
    display_index_for_phase,
    read_shared_state_snapshot,
)
from fun_time.robot_hand.state import SharedState


def test_read_shared_state_snapshot_copies_fields():
    state = SharedState(
        auto_active=True,
        visible=True,
        raw_bpm=120.0,
        beats=4,
        stroke_name="pull",
        pattern_duration=1.5,
        sync_pulse_id=7,
        last_msg="AUTO 1",
        error="boom",
    )

    snapshot = read_shared_state_snapshot(state)

    assert snapshot == SharedStateSnapshot(
        auto_active=True,
        visible=True,
        raw_bpm=120.0,
        beats=4,
        stroke_name="pull",
        pattern_duration=1.5,
        sync_pulse_id=7,
        last_msg="AUTO 1",
        error="boom",
    )


def test_display_index_for_phase_reverses_phase_position():
    assert display_index_for_phase(
        phase=0.25,
        frame_count=8,
        auto_active=True,
        current_frame_index=None,
    ) == 5


def test_display_index_for_phase_clamps_past_end():
    assert display_index_for_phase(
        phase=1.0,
        frame_count=8,
        auto_active=True,
        current_frame_index=None,
    ) == 0


def test_display_index_for_phase_uses_current_frame_when_not_auto_active():
    assert display_index_for_phase(
        phase=0.25,
        frame_count=8,
        auto_active=False,
        current_frame_index=3,
    ) == 3
