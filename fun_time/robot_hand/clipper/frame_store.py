from __future__ import annotations

from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from .state import VideoState


def load_range(cap: cv2.VideoCapture, start_idx: int, end_idx: int) -> dict[int, np.ndarray]:
    result: dict[int, np.ndarray] = {}
    if end_idx < start_idx:
        return result
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_idx)
    idx = start_idx
    while idx <= end_idx:
        ok, frame = cap.read()
        if not ok:
            break
        result[idx] = frame
        idx += 1
    return result


def ensure_loaded(state: VideoState, want_start: int, want_end: int) -> None:
    want_start = max(0, want_start)
    want_end = min(state.total_frames - 1, want_end)
    changed = False
    if want_start < state.loaded_start:
        state.frames.update(load_range(state.cap, want_start, state.loaded_start - 1))
        state.loaded_start = want_start
        changed = True
    if want_end > state.loaded_end:
        new_frames = load_range(state.cap, state.loaded_end + 1, want_end)
        state.frames.update(new_frames)
        state.loaded_end = max(state.loaded_end, max(new_frames.keys(), default=state.loaded_end))
        changed = True
    if changed:
        state.render_rev += 1


def prune_loaded_caches(state: VideoState) -> None:
    for cache in (state.frames, state.frame_signatures):
        for idx in list(cache):
            if idx < state.loaded_start or idx > state.loaded_end:
                del cache[idx]


def safe_frame(state: VideoState, idx: int) -> np.ndarray:
    frame = state.frames.get(idx)
    if frame is None:
        ensure_loaded(state, idx, idx)
        frame = state.frames.get(idx)
    if frame is None:
        raise RuntimeError(f"Could not load frame {idx}")
    return frame


def preprocess_frame_signature(frame: np.ndarray, width: int = 96) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    new_h = max(1, int(round(h * (width / max(1, w)))))
    gray = cv2.resize(gray, (width, new_h), interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    return gray.astype(np.float32) / 255.0


def structural_similarity_score(img1: np.ndarray, img2: np.ndarray) -> float:
    c1 = 0.01 ** 2
    c2 = 0.03 ** 2

    mu1 = cv2.GaussianBlur(img1, (11, 11), 1.5)
    mu2 = cv2.GaussianBlur(img2, (11, 11), 1.5)

    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2

    sigma1_sq = cv2.GaussianBlur(img1 * img1, (11, 11), 1.5) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(img2 * img2, (11, 11), 1.5) - mu2_sq
    sigma12 = cv2.GaussianBlur(img1 * img2, (11, 11), 1.5) - mu1_mu2

    num = (2 * mu1_mu2 + c1) * (2 * sigma12 + c2)
    den = (mu1_sq + mu2_sq + c1) * (sigma1_sq + sigma2_sq + c2)
    ssim_map = num / (den + 1e-12)
    return float(np.mean(ssim_map))


def signature_for_index(state: VideoState, idx: int) -> np.ndarray:
    signature = state.frame_signatures.get(idx)
    if signature is None:
        signature = preprocess_frame_signature(safe_frame(state, idx))
        state.frame_signatures[idx] = signature
    return signature
