from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import subprocess

import cv2
import numpy as np

from .paths import LAST_SESSION_FILE, SESSIONS_DIR
from .utils import safe_atomic_write_json, sanitize_name

LOOP_MODE_BASE_TIP_BASE = "base-tip-base"
LOOP_MODE_TIP_BASE_TIP = "tip-base-tip"
LOOP_MODE_BASE_TIP = "base-tip"
LOOP_MODE_TIP_BASE = "tip-base"
LOOP_MODES = (
    LOOP_MODE_BASE_TIP_BASE,
    LOOP_MODE_TIP_BASE_TIP,
    LOOP_MODE_BASE_TIP,
    LOOP_MODE_TIP_BASE,
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
        return {
            "version": 1,
            "session_name": self.session_name,
            "video_path": self.path,
            "fps": self.fps,
            "total_frames": self.total_frames,
            "loaded_start": self.loaded_start,
            "loaded_end": self.loaded_end,
            "active_start": self.active_start,
            "active_end": self.active_end,
            "current": self.current,
            "seconds_per_step": self.base_step / self.fps,
            "loop_mode": self.loop_mode,
            "wrap_mode": self.wrap_mode,
            "speed": self.speed,
        }

    def autosave_session(self) -> None:
        payload = self.current_payload()
        ok, detail = safe_atomic_write_json(Path(self.session_path), payload)
        if ok:
            self.session_warning = ""
            self.last_saved_payload = payload
            try:
                LAST_SESSION_FILE.write_text(self.session_path, encoding="utf-8")
            except Exception:
                pass
        else:
            self.session_warning = f"Autosave failed: {detail}"


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


def contract_left(state: VideoState) -> None:
    if state.active_start - state.loaded_start >= state.base_step:
        state.loaded_start += state.base_step
        state.current = max(state.current, state.loaded_start)
        update_loop_suggestions(state)
        state.mark_dirty()


def extend_left(state: VideoState) -> None:
    new_start = max(0, state.loaded_start - state.base_step)
    ensure_loaded(state, new_start, state.loaded_end)
    if new_start != state.loaded_start:
        state.loaded_start = new_start
    update_loop_suggestions(state)
    state.mark_dirty()


def contract_right(state: VideoState) -> None:
    if state.loaded_end - state.active_end >= state.base_step:
        state.loaded_end -= state.base_step
        state.current = min(state.current, state.loaded_end)
        update_loop_suggestions(state)
        state.mark_dirty()


def extend_right(state: VideoState) -> None:
    new_end = min(state.total_frames - 1, state.loaded_end + state.base_step)
    ensure_loaded(state, state.loaded_start, new_end)
    if new_end != state.loaded_end:
        state.loaded_end = new_end
    update_loop_suggestions(state)
    state.mark_dirty()


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


def _smooth_1d(values: np.ndarray, radius: int) -> np.ndarray:
    if radius <= 0 or len(values) == 0:
        return values.copy()
    kernel = np.ones(radius * 2 + 1, dtype=np.float64)
    kernel /= kernel.sum()
    padded = np.pad(values, (radius, radius), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def _candidate_similarity_curve(state: VideoState, ref_idx: int, *, direction: int) -> tuple[list[int], np.ndarray] | None:
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
    smoothed = _smooth_1d(scores, smooth_radius)
    return candidates, smoothed


def _find_similarity_dip(state: VideoState, ref_idx: int, *, direction: int) -> tuple[list[int], np.ndarray, int, float, np.ndarray, int] | None:
    curve = _candidate_similarity_curve(state, ref_idx, direction=direction)
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


def _best_duplicate_match_index(state: VideoState, ref_idx: int, *, direction: int) -> int | None:
    dip = _find_similarity_dip(state, ref_idx, direction=direction)
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


def _best_turning_point_index(state: VideoState, ref_idx: int, *, direction: int) -> int | None:
    dip = _find_similarity_dip(state, ref_idx, direction=direction)
    if dip is None:
        return None
    candidates, _smoothed, dip_idx, _baseline, _slope, _run = dip
    return candidates[dip_idx]


def _pair_transition_score(state: VideoState, active_start: int, active_end: int) -> float:
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


def current_loop_frame_index(state: VideoState) -> int:
    sequence = loop_preview_indices(state)
    count = len(sequence)
    if count == 1:
        state.paused_loop_pos = 0
        state.paused_loop_idx = sequence[0]
        return sequence[0]
    if state.loop_paused:
        paused_pos = state.paused_loop_pos
        if paused_pos is None:
            paused_frame = state.paused_loop_idx if state.paused_loop_idx is not None else sequence[0]
            paused_pos = sequence.index(paused_frame) if paused_frame in sequence else 0
        paused_pos = max(0, min(count - 1, paused_pos))
        state.paused_loop_pos = paused_pos
        state.paused_loop_idx = sequence[paused_pos]
        return sequence[paused_pos]
    elapsed = time.monotonic() - state.loop_anchor
    offset = int(elapsed * state.fps * state.speed) % count
    idx = sequence[offset]
    state.paused_loop_pos = offset
    state.paused_loop_idx = idx
    return idx


def loop_preview_indices(state: VideoState) -> list[int]:
    forward = list(range(state.active_start, state.active_end + 1))
    if not forward:
        return [state.active_start]
    if state.loop_mode == LOOP_MODE_TIP_BASE_TIP:
        shift = max(1, len(forward) // 2)
        return forward[shift:] + forward[:shift]
    if state.loop_mode == LOOP_MODE_BASE_TIP:
        return forward + forward[-2::-1]
    if state.loop_mode == LOOP_MODE_TIP_BASE:
        backward = list(reversed(forward))
        return backward[:-1] + forward
    return forward


def timeline_x_for_index(state: VideoState, x1: int, x2: int, idx: int) -> int:
    count = max(1, state.loaded_count - 1)
    frac = (idx - state.loaded_start) / count
    return int(round(x1 + frac * (x2 - x1)))


def index_for_timeline_x(state: VideoState, x1: int, x2: int, x: int) -> int:
    x = max(x1, min(x2, x))
    frac = 0.0 if x2 <= x1 else (x - x1) / (x2 - x1)
    idx = state.loaded_start + int(round(frac * max(1, state.loaded_count - 1)))
    return max(state.loaded_start, min(state.loaded_end, idx))


def set_mark_in(state: VideoState) -> None:
    if state.current < state.active_end:
        state.active_start = state.current
        state.suggestion_anchor_in = state.active_start
        state.reset_loop_anchor()
        update_loop_suggestions(state)
        state.mark_dirty()


def set_mark_out(state: VideoState) -> None:
    if state.current > state.active_start:
        state.active_end = state.current
        state.suggestion_anchor_out = state.active_end
        state.reset_loop_anchor()
        update_loop_suggestions(state)
        state.mark_dirty()


def accept_suggested_in(state: VideoState) -> None:
    if state.suggested_in is None or state.suggested_in >= state.active_end:
        return
    state.active_start = state.suggested_in
    state.suggestion_anchor_in = state.active_start
    state.reset_loop_anchor()
    update_loop_suggestions(state)
    state.mark_dirty()


def accept_suggested_out(state: VideoState) -> None:
    if state.suggested_out is None or state.suggested_out <= state.active_start:
        return
    state.active_end = state.suggested_out
    state.suggestion_anchor_out = state.active_end
    state.reset_loop_anchor()
    update_loop_suggestions(state)
    state.mark_dirty()


def shift_active_range(state: VideoState, direction: int) -> None:
    if direction == 0:
        return
    shift = (state.active_end - state.active_start) * (1 if direction > 0 else -1)
    if shift == 0:
        return

    new_start = state.active_start + shift
    new_end = state.active_end + shift
    if new_start < 0 or new_end >= state.total_frames:
        return

    want_start = new_start
    want_end = new_end
    if direction > 0:
        want_end = min(state.total_frames - 1, new_end + state.base_step)
    else:
        want_start = max(0, new_start - state.base_step)

    ensure_loaded(state, want_start, want_end)
    state.active_start = new_start
    state.active_end = new_end
    state.suggestion_anchor_in = state.active_start
    state.suggestion_anchor_out = state.active_end
    state.current += shift
    state.clamp_current()
    state.reset_loop_anchor()
    update_loop_suggestions(state)
    state.mark_dirty()


def toggle_wrap_mode(state: VideoState) -> None:
    state.wrap_mode = "yellow" if state.wrap_mode == "blue" else "blue"
    if state.wrap_mode == "yellow":
        state.current = max(state.active_start, min(state.active_end, state.current))
    state.mark_dirty()


def cycle_loop_mode(state: VideoState, step: int = 1) -> None:
    current_idx = LOOP_MODES.index(state.loop_mode) if state.loop_mode in LOOP_MODES else 0
    state.loop_mode = LOOP_MODES[(current_idx + step) % len(LOOP_MODES)]
    state.mark_dirty()


def move_current_left(state: VideoState) -> None:
    low = state.loaded_start if state.wrap_mode == "blue" else state.active_start
    high = state.loaded_end if state.wrap_mode == "blue" else state.active_end
    if state.current <= low:
        state.current = high
    else:
        state.current -= 1
    state.render_rev += 1


def move_current_right(state: VideoState) -> None:
    low = state.loaded_start if state.wrap_mode == "blue" else state.active_start
    high = state.loaded_end if state.wrap_mode == "blue" else state.active_end
    if state.current >= high:
        state.current = low
    else:
        state.current += 1
    state.render_rev += 1


def change_speed(state: VideoState, delta: float) -> None:
    old_speed = state.speed
    _ = current_loop_frame_index(state)
    new_speed = max(0.25, min(2.0, round((state.speed + delta) * 4) / 4))
    if new_speed == old_speed:
        return
    offset = state.paused_loop_pos if state.paused_loop_pos is not None else 0
    state.speed = new_speed
    state.loop_anchor = time.monotonic() - (offset / max(1e-9, state.fps * state.speed))
    if not state.loop_paused:
        state.paused_loop_idx = None
        state.paused_loop_pos = None
    state.render_rev += 1


def toggle_loop_pause(state: VideoState) -> None:
    current_idx = current_loop_frame_index(state)
    current_pos = state.paused_loop_pos if state.paused_loop_pos is not None else 0
    if state.loop_paused:
        state.loop_paused = False
        state.paused_loop_idx = None
        state.paused_loop_pos = None
        state.loop_anchor = time.monotonic() - (current_pos / max(1e-9, state.fps * state.speed))
    else:
        state.loop_paused = True
        state.paused_loop_idx = current_idx
        state.paused_loop_pos = current_pos
    state.render_rev += 1


def make_video_state(
    video_path: str,
    session_name: str,
    start_time_s: float,
    seconds: float,
    loop_mode: str = LOOP_MODE_BASE_TIP_BASE,
    payload_override: dict[str, Any] | None = None,
) -> VideoState:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(round(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    if not fps or fps <= 0:
        raise RuntimeError("Invalid FPS metadata")
    if total_frames <= 0:
        raise RuntimeError("Invalid frame count metadata")
    base_step = max(1, int(round(fps)))

    if payload_override is None:
        if loop_mode not in LOOP_MODES:
            loop_mode = LOOP_MODE_BASE_TIP_BASE
        start_idx = max(0, min(total_frames - 1, int(round(start_time_s * fps))))
        duration_frames = max(1, int(round(seconds * fps)))
        end_idx = min(total_frames - 1, start_idx + duration_frames - 1)
        loaded_start = start_idx
        loaded_end = end_idx
        active_start = start_idx
        active_end = end_idx
        current = start_idx
        wrap_mode = "blue"
        speed = 1.0
        original_payload = {
            "version": 1,
            "session_name": session_name,
            "video_path": video_path,
            "fps": fps,
            "total_frames": total_frames,
            "loaded_start": loaded_start,
            "loaded_end": loaded_end,
            "active_start": active_start,
            "active_end": active_end,
            "current": current,
            "seconds_per_step": base_step / fps,
            "loop_mode": loop_mode,
            "wrap_mode": wrap_mode,
            "speed": speed,
        }
    else:
        original_payload = dict(payload_override)
        loaded_start = int(payload_override["loaded_start"])
        loaded_end = int(payload_override["loaded_end"])
        active_start = int(payload_override["active_start"])
        active_end = int(payload_override["active_end"])
        current = int(payload_override.get("current", active_start))
        loop_mode = str(payload_override.get("loop_mode", LOOP_MODE_BASE_TIP_BASE))
        if loop_mode not in LOOP_MODES:
            loop_mode = LOOP_MODE_BASE_TIP_BASE
        wrap_mode = payload_override.get("wrap_mode", "blue")
        speed = float(payload_override.get("speed", 1.0))
        speed = max(0.25, min(2.0, round(speed * 4) / 4))
        session_name = payload_override.get("session_name", session_name)
        video_path = payload_override["video_path"]

    frames = load_range(cap, loaded_start, loaded_end)
    if not frames:
        raise RuntimeError("No frames were extracted for the requested/session interval")
    state = VideoState(
        cap=cap,
        path=video_path,
        fps=fps,
        total_frames=total_frames,
        loaded_start=loaded_start,
        loaded_end=max(frames.keys()),
        active_start=active_start,
        active_end=active_end,
        current=current,
        base_step=base_step,
        frames=frames,
        loop_anchor=time.monotonic(),
        session_name=session_name,
        session_path=str(SESSIONS_DIR / f"{sanitize_name(session_name)}.json"),
        original_session_payload=original_payload,
        loop_mode=loop_mode,
        wrap_mode=wrap_mode,
        speed=speed,
        initial_active_start=active_start,
        initial_active_end=active_end,
        suggestion_anchor_in=active_start,
        suggestion_anchor_out=active_end,
    )
    state.clamp_current()
    state.last_saved_payload = state.current_payload()
    update_loop_suggestions(state)
    return state


def restore_original_session(state: VideoState) -> None:
    safe_atomic_write_json(Path(state.session_path), state.original_session_payload)
