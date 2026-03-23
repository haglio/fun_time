from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fun_time.robot_hand.clipper.session_persistence import (
    autosave_session,
    current_payload,
    restore_original_session,
)


def _state():
    return SimpleNamespace(
        session_name="demo",
        path="/video.mp4",
        fps=30.0,
        total_frames=120,
        loaded_start=10,
        loaded_end=40,
        active_start=12,
        active_end=30,
        current=18,
        base_step=30,
        loop_mode="base-tip-base",
        wrap_mode="blue",
        speed=1.25,
        session_path="C:\\demo.json",
        session_warning="",
        last_saved_payload=None,
        original_session_payload={"version": 1},
    )


def test_current_payload_builds_expected_fields():
    payload = current_payload(_state())

    assert payload == {
        "version": 1,
        "session_name": "demo",
        "video_path": "/video.mp4",
        "fps": 30.0,
        "total_frames": 120,
        "loaded_start": 10,
        "loaded_end": 40,
        "active_start": 12,
        "active_end": 30,
        "current": 18,
        "seconds_per_step": 1.0,
        "loop_mode": "base-tip-base",
        "wrap_mode": "blue",
        "speed": 1.25,
    }


def test_autosave_session_updates_last_saved_payload_on_success():
    state = _state()
    last_session_file = MagicMock()

    with patch("fun_time.robot_hand.clipper.session_persistence.safe_atomic_write_json", return_value=(True, "")), \
         patch("fun_time.robot_hand.clipper.session_persistence.LAST_SESSION_FILE", last_session_file):
        autosave_session(state)

    assert state.session_warning == ""
    assert state.last_saved_payload == current_payload(state)
    last_session_file.write_text.assert_called_once_with("C:\\demo.json", encoding="utf-8")


def test_autosave_session_records_failure_message():
    state = _state()

    with patch("fun_time.robot_hand.clipper.session_persistence.safe_atomic_write_json", return_value=(False, "disk full")):
        autosave_session(state)

    assert state.session_warning == "Autosave failed: disk full"
    assert state.last_saved_payload is None


def test_restore_original_session_writes_original_payload():
    state = _state()

    with patch("fun_time.robot_hand.clipper.session_persistence.safe_atomic_write_json") as write_json:
        restore_original_session(state)

    write_json.assert_called_once_with(Path("C:\\demo.json"), {"version": 1})
