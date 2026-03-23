from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SharedStateSnapshot:
    auto_active: bool
    visible: bool
    raw_bpm: float | None
    beats: int | None
    stroke_name: str
    pattern_duration: float | None
    sync_pulse_id: int
    last_msg: str
    error: str | None


def read_shared_state_snapshot(state) -> SharedStateSnapshot:
    with state.lock:
        return SharedStateSnapshot(
            auto_active=state.auto_active,
            visible=state.visible,
            raw_bpm=state.raw_bpm,
            beats=state.beats,
            stroke_name=state.stroke_name,
            pattern_duration=state.pattern_duration,
            sync_pulse_id=state.sync_pulse_id,
            last_msg=state.last_msg,
            error=state.error,
        )


def display_index_for_phase(
    *,
    phase: float,
    frame_count: int,
    auto_active: bool,
    current_frame_index: int | None,
) -> int:
    logical_index = int(phase * frame_count)
    if logical_index >= frame_count:
        logical_index = frame_count - 1

    display_index = (frame_count - 1) - logical_index
    if not auto_active and current_frame_index is not None:
        return current_frame_index
    return display_index
