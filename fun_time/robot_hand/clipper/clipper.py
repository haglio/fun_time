#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from fun_time.robot_hand.clipper.app import main
    from fun_time.robot_hand.clipper.paths import AUDIO_DIR, CLIP_POSTPROCESS_SCRIPT, CLIPS_DIR, LAST_SESSION_FILE, LOOP_FIX_SCRIPT, MODULE_DIR, RAW_CLIPS_DIR, ROBOT_HAND_DIR, SESSIONS_DIR
else:
    from .app import main
    from .paths import AUDIO_DIR, CLIP_POSTPROCESS_SCRIPT, CLIPS_DIR, LAST_SESSION_FILE, LOOP_FIX_SCRIPT, MODULE_DIR, RAW_CLIPS_DIR, ROBOT_HAND_DIR, SESSIONS_DIR


__all__ = [
    "AUDIO_DIR",
    "CLIP_POSTPROCESS_SCRIPT",
    "CLIPS_DIR",
    "LAST_SESSION_FILE",
    "LOOP_FIX_SCRIPT",
    "MODULE_DIR",
    "RAW_CLIPS_DIR",
    "ROBOT_HAND_DIR",
    "SESSIONS_DIR",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
