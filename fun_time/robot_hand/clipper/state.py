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
    exit_prompt_visible: bool = False
    exit_prompt_action: str = ""

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
        state.mark_dirty()


def extend_left(state: VideoState) -> None:
    new_start = max(0, state.loaded_start - state.base_step)
    ensure_loaded(state, new_start, state.loaded_end)
    if new_start != state.loaded_start:
        state.loaded_start = new_start
    state.mark_dirty()


def contract_right(state: VideoState) -> None:
    if state.loaded_end - state.active_end >= state.base_step:
        state.loaded_end -= state.base_step
        state.current = min(state.current, state.loaded_end)
        state.mark_dirty()


def extend_right(state: VideoState) -> None:
    new_end = min(state.total_frames - 1, state.loaded_end + state.base_step)
    ensure_loaded(state, state.loaded_start, new_end)
    if new_end != state.loaded_end:
        state.loaded_end = new_end
    state.mark_dirty()


def safe_frame(state: VideoState, idx: int) -> np.ndarray:
    frame = state.frames.get(idx)
    if frame is None:
        ensure_loaded(state, idx, idx)
        frame = state.frames.get(idx)
    if frame is None:
        raise RuntimeError(f"Could not load frame {idx}")
    return frame


def current_loop_frame_index(state: VideoState) -> int:
    count = max(1, state.active_count)
    if count == 1:
        state.paused_loop_idx = state.active_start
        return state.active_start
    if state.loop_paused:
        paused = state.paused_loop_idx if state.paused_loop_idx is not None else state.active_start
        return max(state.active_start, min(state.active_end, paused))
    elapsed = time.monotonic() - state.loop_anchor
    offset = int(elapsed * state.fps * state.speed) % count
    idx = state.active_start + offset
    state.paused_loop_idx = idx
    return idx


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
        state.reset_loop_anchor()
        state.mark_dirty()


def set_mark_out(state: VideoState) -> None:
    if state.current > state.active_start:
        state.active_end = state.current
        state.reset_loop_anchor()
        state.mark_dirty()


def toggle_wrap_mode(state: VideoState) -> None:
    state.wrap_mode = "yellow" if state.wrap_mode == "blue" else "blue"
    if state.wrap_mode == "yellow":
        state.current = max(state.active_start, min(state.active_end, state.current))
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
    current_idx = current_loop_frame_index(state)
    new_speed = max(0.25, min(2.0, round((state.speed + delta) * 4) / 4))
    if new_speed == old_speed:
        return
    offset = max(0, current_idx - state.active_start)
    state.speed = new_speed
    state.loop_anchor = time.monotonic() - (offset / max(1e-9, state.fps * state.speed))
    if not state.loop_paused:
        state.paused_loop_idx = None
    state.render_rev += 1


def toggle_loop_pause(state: VideoState) -> None:
    current_idx = current_loop_frame_index(state)
    if state.loop_paused:
        offset = max(0, current_idx - state.active_start)
        state.loop_paused = False
        state.paused_loop_idx = None
        state.loop_anchor = time.monotonic() - (offset / max(1e-9, state.fps * state.speed))
    else:
        state.loop_paused = True
        state.paused_loop_idx = current_idx
    state.render_rev += 1


def make_video_state(
    video_path: str,
    session_name: str,
    start_time_s: float,
    seconds: float,
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
        wrap_mode=wrap_mode,
        speed=speed,
    )
    state.clamp_current()
    state.last_saved_payload = state.current_payload()
    return state


def restore_original_session(state: VideoState) -> None:
    safe_atomic_write_json(Path(state.session_path), state.original_session_payload)
