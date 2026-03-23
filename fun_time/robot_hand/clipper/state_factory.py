from __future__ import annotations

import time
from typing import Any

import cv2

from .frame_store import load_range
from .loop_modes import LOOP_MODE_BASE_TIP_BASE, LOOP_MODES
from .paths import SESSIONS_DIR
from .state import VideoState, update_loop_suggestions
from .utils import sanitize_name


def _normalized_loop_mode(loop_mode: str) -> str:
    return loop_mode if loop_mode in LOOP_MODES else LOOP_MODE_BASE_TIP_BASE


def _normalized_speed(speed: float) -> float:
    return max(0.25, min(2.0, round(speed * 4) / 4))


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
        loop_mode = _normalized_loop_mode(loop_mode)
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
        loop_mode = _normalized_loop_mode(str(payload_override.get("loop_mode", LOOP_MODE_BASE_TIP_BASE)))
        wrap_mode = payload_override.get("wrap_mode", "blue")
        speed = _normalized_speed(float(payload_override.get("speed", 1.0)))
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
