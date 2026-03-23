from __future__ import annotations

from typing import TYPE_CHECKING

from .frame_store import signature_for_index, structural_similarity_score
from .loop_modes import LOOP_MODE_BASE_TIP, LOOP_MODE_TIP_BASE
from .suggestion_search import (
    best_duplicate_match_index,
    best_turning_point_index,
    pair_transition_score,
)

if TYPE_CHECKING:
    from .state import VideoState


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
