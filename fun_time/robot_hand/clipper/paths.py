from __future__ import annotations

from pathlib import Path

WIN_LEFT_KEYS = {2424832, 81}
WIN_RIGHT_KEYS = {2555904, 83}
ESC_KEYS = {27}
QUIT_KEYS = {ord("q"), ord("Q")}
MARK_IN_KEYS = {ord("i"), ord("I"), ord("["), 91}
MARK_OUT_KEYS = {ord("o"), ord("O"), ord("]"), 93}
ACCEPT_SUGGESTED_IN_KEYS = {ord("9")}
ACCEPT_SUGGESTED_OUT_KEYS = {ord("0")}
WRAP_TOGGLE_KEYS = {ord("m"), ord("M")}
PLAY_PAUSE_KEYS = {32}
SPEED_DOWN_KEYS = {ord("-"), ord("_")}
SPEED_UP_KEYS = {ord("+"), ord("="), ord("=")}
ENTER_KEYS = {13, 10}

MODULE_DIR = Path(__file__).resolve().parent
ROBOT_HAND_DIR = MODULE_DIR.parent
SESSIONS_DIR = MODULE_DIR / "sessions"
RAW_CLIPS_DIR = MODULE_DIR / "raw_clips"
CLIPS_DIR = ROBOT_HAND_DIR / "clips"
AUDIO_DIR = ROBOT_HAND_DIR / "audio"
LAST_SESSION_FILE = SESSIONS_DIR / ".last_session.txt"
LOOP_FIX_SCRIPT = MODULE_DIR / "loop_fixer_and_sizer.py"


def ensure_runtime_dirs() -> None:
    for directory in (SESSIONS_DIR, RAW_CLIPS_DIR, CLIPS_DIR, AUDIO_DIR):
        directory.mkdir(parents=True, exist_ok=True)


__all__ = [
    "AUDIO_DIR",
    "ACCEPT_SUGGESTED_IN_KEYS",
    "ACCEPT_SUGGESTED_OUT_KEYS",
    "CLIPS_DIR",
    "ENTER_KEYS",
    "ESC_KEYS",
    "LAST_SESSION_FILE",
    "LOOP_FIX_SCRIPT",
    "MARK_IN_KEYS",
    "MARK_OUT_KEYS",
    "MODULE_DIR",
    "PLAY_PAUSE_KEYS",
    "QUIT_KEYS",
    "RAW_CLIPS_DIR",
    "ROBOT_HAND_DIR",
    "SESSIONS_DIR",
    "SPEED_DOWN_KEYS",
    "SPEED_UP_KEYS",
    "WIN_LEFT_KEYS",
    "WIN_RIGHT_KEYS",
    "WRAP_TOGGLE_KEYS",
    "ensure_runtime_dirs",
]
