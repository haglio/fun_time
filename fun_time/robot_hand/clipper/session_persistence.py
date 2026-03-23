from __future__ import annotations

from pathlib import Path

from .paths import LAST_SESSION_FILE
from .utils import safe_atomic_write_json


def current_payload(state) -> dict:
    return {
        "version": 1,
        "session_name": state.session_name,
        "video_path": state.path,
        "fps": state.fps,
        "total_frames": state.total_frames,
        "loaded_start": state.loaded_start,
        "loaded_end": state.loaded_end,
        "active_start": state.active_start,
        "active_end": state.active_end,
        "current": state.current,
        "seconds_per_step": state.base_step / state.fps,
        "loop_mode": state.loop_mode,
        "wrap_mode": state.wrap_mode,
        "speed": state.speed,
    }


def autosave_session(state) -> None:
    payload = current_payload(state)
    ok, detail = safe_atomic_write_json(Path(state.session_path), payload)
    if ok:
        state.session_warning = ""
        state.last_saved_payload = payload
        try:
            LAST_SESSION_FILE.write_text(state.session_path, encoding="utf-8")
        except Exception:
            pass
    else:
        state.session_warning = f"Autosave failed: {detail}"


def restore_original_session(state) -> None:
    safe_atomic_write_json(Path(state.session_path), state.original_session_payload)
