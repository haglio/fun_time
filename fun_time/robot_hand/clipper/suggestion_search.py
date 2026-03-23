from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np


def smooth_1d(values: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0 or len(values) == 0:
        return values.copy()
    kernel = np.ones(radius * 2 + 1, dtype=np.float64)
    kernel /= kernel.sum()
    padded = np.pad(values, (radius, radius), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def candidate_similarity_curve(
    state: Any,
    ref_idx: int,
    *,
    direction: int,
    signature_for_index: Callable[[Any, int], np.ndarray],
    structural_similarity_score: Callable[[np.ndarray, np.ndarray], float],
) -> tuple[list[int], np.ndarray] | None:
    min_gap = 10
    if direction > 0:
        candidates = list(range(ref_idx + min_gap, state.loaded_end + 1))
    else:
        candidates = list(range(state.loaded_start, ref_idx - min_gap + 1))
    if not candidates:
        return None

    ref_signature = signature_for_index(state, ref_idx)
    scores = np.asarray(
        [structural_similarity_score(ref_signature, signature_for_index(state, idx)) for idx in candidates],
        dtype=np.float64,
    )
    if len(scores) < 5:
        return None

    smooth_radius = max(1, int(round(state.fps * 0.03)))
    smoothed = smooth_1d(scores, smooth_radius)
    return candidates, smoothed


def find_similarity_dip(
    state: Any,
    ref_idx: int,
    *,
    direction: int,
    signature_for_index: Callable[[Any, int], np.ndarray],
    structural_similarity_score: Callable[[np.ndarray, np.ndarray], float],
) -> tuple[list[int], np.ndarray, int, float, np.ndarray, int] | None:
    curve = candidate_similarity_curve(
        state,
        ref_idx,
        direction=direction,
        signature_for_index=signature_for_index,
        structural_similarity_score=structural_similarity_score,
    )
    if curve is None:
        return None
    candidates, smoothed = curve
    skip = min(len(smoothed) - 1, max(1, int(round(state.fps * 0.12))))
    baseline_end = min(len(smoothed), max(skip + 1, int(round(state.fps * 0.18))))
    baseline = float(np.max(smoothed[:baseline_end]))
    slope = np.diff(smoothed)
    run = max(2, int(round(state.fps * 0.05)))
    dip_idx: int | None = None

    for i in range(max(skip + run, run), len(smoothed) - run - 1):
        if baseline - smoothed[i] < 0.02:
            continue
        pre = float(np.mean(slope[i - run:i]))
        post = float(np.mean(slope[i:i + run]))
        if pre < -0.0005 and post > 0.0005:
            dip_idx = i
            break

    if dip_idx is None:
        return None
    return candidates, smoothed, dip_idx, baseline, slope, run


def best_duplicate_match_index(
    state: Any,
    ref_idx: int,
    *,
    direction: int,
    signature_for_index: Callable[[Any, int], np.ndarray],
    structural_similarity_score: Callable[[np.ndarray, np.ndarray], float],
) -> int | None:
    dip = find_similarity_dip(
        state,
        ref_idx,
        direction=direction,
        signature_for_index=signature_for_index,
        structural_similarity_score=structural_similarity_score,
    )
    if dip is None:
        return None
    candidates, smoothed, dip_idx, baseline, slope, run = dip

    peak_idx: int | None = None
    min_rebound = max(0.004, (baseline - smoothed[dip_idx]) * 0.10)
    for i in range(dip_idx + run + 1, len(smoothed) - run - 1):
        rebound = smoothed[i] - smoothed[dip_idx]
        if rebound < min_rebound:
            continue
        pre = float(np.mean(slope[i - run:i]))
        post = float(np.mean(slope[i:i + run]))
        if pre > 0.0002 and post < -0.0002:
            peak_idx = i
            break

    if peak_idx is None:
        return None

    viable = np.where(smoothed[dip_idx + 1:] >= (smoothed[dip_idx] + min_rebound))[0]
    if len(viable) == 0:
        return None
    lo = dip_idx + 1 + int(viable[0])
    ref_signature = signature_for_index(state, ref_idx)
    raw_scores = np.asarray(
        [structural_similarity_score(ref_signature, signature_for_index(state, idx)) for idx in candidates],
        dtype=np.float64,
    )
    refined = lo + int(np.argmax(raw_scores[lo:]))
    return candidates[refined]


def best_turning_point_index(
    state: Any,
    ref_idx: int,
    *,
    direction: int,
    signature_for_index: Callable[[Any, int], np.ndarray],
    structural_similarity_score: Callable[[np.ndarray, np.ndarray], float],
) -> int | None:
    dip = find_similarity_dip(
        state,
        ref_idx,
        direction=direction,
        signature_for_index=signature_for_index,
        structural_similarity_score=structural_similarity_score,
    )
    if dip is None:
        return None
    candidates, _smoothed, dip_idx, _baseline, _slope, _run = dip
    return candidates[dip_idx]


def pair_transition_score(
    state: Any,
    active_start: int,
    active_end: int,
    *,
    signature_for_index: Callable[[Any, int], np.ndarray],
    structural_similarity_score: Callable[[np.ndarray, np.ndarray], float],
) -> float:
    scores: list[float] = []
    if active_end + 1 <= state.loaded_end:
        scores.append(
            structural_similarity_score(
                signature_for_index(state, active_start),
                signature_for_index(state, active_end + 1),
            )
        )
    if active_start - 1 >= state.loaded_start:
        scores.append(
            structural_similarity_score(
                signature_for_index(state, active_start - 1),
                signature_for_index(state, active_end),
            )
        )
    if not scores:
        return float("-inf")
    return float(sum(scores) / len(scores))
