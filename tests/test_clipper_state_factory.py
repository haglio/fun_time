from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from fun_time.robot_hand.clipper.loop_modes import LOOP_MODE_BASE_TIP_BASE
from fun_time.robot_hand.clipper.state import make_video_state


def _build_capture(*, fps: float = 30.0, total_frames: float = 120.0) -> MagicMock:
    cap = MagicMock()
    cap.isOpened.return_value = True
    cap.get.side_effect = [fps, total_frames]
    return cap


def test_make_video_state_defaults_invalid_new_session_loop_mode():
    cap = _build_capture()
    frames = {i: np.zeros((2, 2, 3), dtype=np.uint8) for i in range(30)}

    with patch("fun_time.robot_hand.clipper.state.cv2.VideoCapture", return_value=cap):
        with patch("fun_time.robot_hand.clipper.state.load_range", return_value=frames):
            state = make_video_state("/fake/video.mp4", "demo", 0.0, 1.0, loop_mode="not-a-mode")

    assert state.loop_mode == LOOP_MODE_BASE_TIP_BASE


def test_make_video_state_defaults_invalid_payload_loop_mode_and_clamps_speed():
    cap = _build_capture()
    frames = {i: np.zeros((2, 2, 3), dtype=np.uint8) for i in range(10, 41)}
    payload = {
        "video_path": "/video.mp4",
        "session_name": "demo",
        "loaded_start": 10,
        "loaded_end": 40,
        "active_start": 12,
        "active_end": 35,
        "current": 18,
        "seconds_per_step": 1.0,
        "fps": 30.0,
        "total_frames": 120,
        "loop_mode": "still-not-a-mode",
        "wrap_mode": "yellow",
        "speed": 2.3,
    }

    with patch("fun_time.robot_hand.clipper.state.cv2.VideoCapture", return_value=cap):
        with patch("fun_time.robot_hand.clipper.state.load_range", return_value=frames):
            state = make_video_state("/fake/video.mp4", "demo", 0.0, 1.0, payload_override=payload)

    assert state.loop_mode == LOOP_MODE_BASE_TIP_BASE
    assert state.speed == pytest.approx(2.0)


def test_make_video_state_raises_when_requested_interval_has_no_frames():
    cap = _build_capture()

    with patch("fun_time.robot_hand.clipper.state.cv2.VideoCapture", return_value=cap):
        with patch("fun_time.robot_hand.clipper.state.load_range", return_value={}):
            with pytest.raises(RuntimeError, match="No frames were extracted"):
                make_video_state("/fake/video.mp4", "demo", 0.0, 1.0)
