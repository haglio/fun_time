from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from .frame_store import (
    signature_for_index,
    structural_similarity_score,
)
from .loop_modes import (
    LOOP_MODE_BASE_TIP,
    LOOP_MODE_BASE_TIP_BASE,
    LOOP_MODE_TIP_BASE,
    LOOP_MODE_TIP_BASE_TIP,
)
from .session_persistence import (
    autosave_session as persist_session_state,
    current_payload as build_current_payload,
    restore_original_session as restore_original_session_payload,
)
from .suggestion_search import (
    best_duplicate_match_index,
    best_turning_point_index,
    candidate_similarity_curve,
    find_similarity_dip,
    pair_transition_score,
)


@dataclass
class ExportJob:
    active: bool = False
    done: bool = False
    failed: bool = False
    dismissed: bool = False
    stage: str = ""
    clip_progress: float = 0.0
    fix_progress: float = 0.0
    audio_progress: float = 0.0
    clip_status: str = "Waiting"
    fix_status: str = "Waiting"
    audio_status: str = "Waiting"
    error_message: str = ""
    raw_clip_output: str = ""
    clip_output: str = ""
    audio_output: str = ""
    worker: threading.Thread | None = None
    procs: list[subprocess.Popen[str]] = field(default_factory=list)


@dataclass
class VideoState:
    cap: cv2.VideoCapture
    path: str
    fps: float
    total_frames: int
    loaded_start: int
    loaded_end: int
    active_start: int
    active_end: int
    current: int
    base_step: int
    frames: dict[int, np.ndarray]
    loop_anchor: float
    session_name: str
    session_path: str
    original_session_payload: dict[str, Any]
    loop_mode: str = LOOP_MODE_BASE_TIP_BASE
    wrap_mode: str = "blue"
    speed: float = 1.0
    export_job: ExportJob | None = None
    session_warning: str = ""
    dirty: bool = False
    protect_existing_save_data: bool = False
    last_saved_payload: dict[str, Any] | None = None
    hover_text: str = ""
    buttons: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)
    mouse_x: int = 0
    mouse_y: int = 0
    render_rev: int = 0
    loop_paused: bool = False
    paused_loop_idx: int | None = None
    paused_loop_pos: int | None = None
    exit_prompt_visible: bool = False
    exit_prompt_focus: str = "save"
    exit_prompt_action: str = ""
    initial_active_start: int | None = None
    initial_active_end: int | None = None
    suggested_in: int | None = None
    suggested_out: int | None = None
    suggestion_anchor_in: int | None = None
    suggestion_anchor_out: int | None = None
    frame_signatures: dict[int, np.ndarray] = field(default_factory=dict)

    @property
    def active_count(self) -> int:
        return self.active_end - self.active_start + 1

    @property
    def loaded_count(self) -> int:
        return self.loaded_end - self.loaded_start + 1

    @property
    def should_prompt_on_exit(self) -> bool:
        return self.dirty and self.protect_existing_save_data

    def clamp_current(self) -> None:
        low = self.loaded_start if self.wrap_mode == "blue" else self.active_start
        high = self.loaded_end if self.wrap_mode == "blue" else self.active_end
        self.current = max(low, min(high, self.current))

    def reset_loop_anchor(self) -> None:
        self.loop_anchor = time.monotonic()

    def mark_dirty(self) -> None:
        self.dirty = True
        self.render_rev += 1
        self.autosave_session()

    def current_payload(self) -> dict[str, Any]:
        return build_current_payload(self)

    def autosave_session(self) -> None:
        persist_session_state(self)

def _candidate_similarity_curve(state: VideoState, ref_idx: int, *, direction: int) -> tuple[list[int], np.ndarray] | None:
    return candidate_similarity_curve(
        state,
        ref_idx,
        direction=direction,
        signature_for_index=signature_for_index,
        structural_similarity_score=structural_similarity_score,
    )


def _find_similarity_dip(state: VideoState, ref_idx: int, *, direction: int) -> tuple[list[int], np.ndarray, int, float, np.ndarray, int] | None:
    return find_similarity_dip(
        state,
        ref_idx,
        direction=direction,
        signature_for_index=signature_for_index,
        structural_similarity_score=structural_similarity_score,
    )


def _best_duplicate_match_index(state: VideoState, ref_idx: int, *, direction: int) -> int | None:
    return best_duplicate_match_index(
        state,
        ref_idx,
        direction=direction,
        signature_for_index=signature_for_index,
        structural_similarity_score=structural_similarity_score,
    )


def _best_turning_point_index(state: VideoState, ref_idx: int, *, direction: int) -> int | None:
    return best_turning_point_index(
        state,
        ref_idx,
        direction=direction,
        signature_for_index=signature_for_index,
        structural_similarity_score=structural_similarity_score,
    )


def _pair_transition_score(state: VideoState, active_start: int, active_end: int) -> float:
    return pair_transition_score(
        state,
        active_start,
        active_end,
        signature_for_index=signature_for_index,
        structural_similarity_score=structural_similarity_score,
    )


def update_loop_suggestions(state: VideoState) -> None:
    initial_start = state.initial_active_start if state.initial_active_start is not None else state.active_start
    initial_end = state.initial_active_end if state.initial_active_end is not None else state.active_end
    start_changed = state.active_start != initial_start
    end_changed = state.active_end != initial_end
    use_turning_point = state.loop_mode in {LOOP_MODE_BASE_TIP, LOOP_MODE_TIP_BASE}

    suggested_in: int | None = None
    suggested_out: int | None = None

    if start_changed:
        if use_turning_point:
            candidate = _best_turning_point_index(state, state.active_start, direction=+1)
            if candidate is not None and state.active_start < candidate <= state.loaded_end:
                suggested_out = candidate
        else:
            match_idx = _best_duplicate_match_index(state, state.active_start, direction=+1)
            if match_idx is not None:
                candidate = match_idx - 1
                if state.active_start < candidate <= state.loaded_end:
                    suggested_out = candidate

    if end_changed:
        if use_turning_point:
            candidate = _best_turning_point_index(state, state.active_end, direction=-1)
            if candidate is not None and state.loaded_start <= candidate < state.active_end:
                suggested_in = candidate
        else:
            match_idx = _best_duplicate_match_index(state, state.active_end, direction=-1)
            if match_idx is not None:
                candidate = match_idx + 1
                if state.loaded_start <= candidate < state.active_end:
                    suggested_in = candidate

    if start_changed and end_changed and not use_turning_point:
        anchor_in = state.suggestion_anchor_in if state.suggestion_anchor_in is not None else state.active_start
        anchor_out = state.suggestion_anchor_out if state.suggestion_anchor_out is not None else state.active_end
        best_pair = (state.active_start, state.active_end)
        best_score = _pair_transition_score(state, *best_pair)
        for start_shift in range(-2, 3):
            candidate_start = anchor_in + start_shift
            if candidate_start < state.loaded_start or candidate_start > state.loaded_end:
                continue
            for end_shift in range(-2, 3):
                candidate_end = anchor_out + end_shift
                if candidate_end < state.loaded_start or candidate_end > state.loaded_end:
                    continue
                if candidate_start >= candidate_end:
                    continue
                score = _pair_transition_score(state, candidate_start, candidate_end)
                if score > best_score + 1e-6:
                    best_score = score
                    best_pair = (candidate_start, candidate_end)
        suggested_in, suggested_out = best_pair

    if state.suggested_in != suggested_in or state.suggested_out != suggested_out:
        state.suggested_in = suggested_in
        state.suggested_out = suggested_out
        state.render_rev += 1


def restore_original_session(state: VideoState) -> None:
    restore_original_session_payload(state)
