from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from .loop_modes import LOOP_MODE_BASE_TIP_BASE
from .session_persistence import (
    autosave_session as persist_session_state,
    current_payload as build_current_payload,
    restore_original_session as restore_original_session_payload,
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


def restore_original_session(state: VideoState) -> None:
    restore_original_session_payload(state)
