"""Tests for fun_time.robot_hand.clipper.paths."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.robot_hand.clipper.paths import (
    AUDIO_DIR,
    CLIPS_DIR,
    ENTER_KEYS,
    ESC_KEYS,
    LAST_SESSION_FILE,
    LOOP_FIX_SCRIPT,
    MARK_IN_KEYS,
    MARK_OUT_KEYS,
    MODULE_DIR,
    QUIT_KEYS,
    RAW_CLIPS_DIR,
    ROBOT_HAND_DIR,
    SESSIONS_DIR,
    SPEED_DOWN_KEYS,
    SPEED_UP_KEYS,
    WIN_LEFT_KEYS,
    WIN_RIGHT_KEYS,
    WRAP_TOGGLE_KEYS,
    ensure_runtime_dirs,
)


# ---------------------------------------------------------------------------
# Module-level path constants
# ---------------------------------------------------------------------------

class TestPathConstants:
    def test_sessions_dir_under_module_dir(self):
        assert SESSIONS_DIR.parent == MODULE_DIR

    def test_raw_clips_dir_under_module_dir(self):
        assert RAW_CLIPS_DIR.parent == MODULE_DIR

    def test_clips_dir_under_robot_hand_dir(self):
        assert CLIPS_DIR.parent == ROBOT_HAND_DIR

    def test_audio_dir_under_robot_hand_dir(self):
        assert AUDIO_DIR.parent == ROBOT_HAND_DIR

    def test_last_session_file_under_sessions_dir(self):
        assert LAST_SESSION_FILE.parent == SESSIONS_DIR

    def test_loop_fix_script_under_module_dir(self):
        assert LOOP_FIX_SCRIPT.parent == MODULE_DIR

    def test_loop_fix_script_is_python_file(self):
        assert LOOP_FIX_SCRIPT.suffix == ".py"

    def test_robot_hand_dir_is_module_parent(self):
        assert ROBOT_HAND_DIR == MODULE_DIR.parent


# ---------------------------------------------------------------------------
# Key binding sets
# ---------------------------------------------------------------------------

class TestKeyBindings:
    def test_esc_keys_contains_27(self):
        assert 27 in ESC_KEYS

    def test_quit_keys_contains_q(self):
        assert ord("q") in QUIT_KEYS
        assert ord("Q") in QUIT_KEYS

    def test_mark_in_contains_i(self):
        assert ord("i") in MARK_IN_KEYS
        assert ord("I") in MARK_IN_KEYS

    def test_mark_out_contains_o(self):
        assert ord("o") in MARK_OUT_KEYS
        assert ord("O") in MARK_OUT_KEYS

    def test_wrap_toggle_contains_m(self):
        assert ord("m") in WRAP_TOGGLE_KEYS
        assert ord("M") in WRAP_TOGGLE_KEYS

    def test_speed_down_contains_minus(self):
        assert ord("-") in SPEED_DOWN_KEYS

    def test_speed_up_contains_plus(self):
        assert ord("+") in SPEED_UP_KEYS

    def test_enter_keys_contains_13(self):
        assert 13 in ENTER_KEYS

    def test_win_left_keys_nonempty(self):
        assert len(WIN_LEFT_KEYS) > 0

    def test_win_right_keys_nonempty(self):
        assert len(WIN_RIGHT_KEYS) > 0

    def test_all_key_sets_are_sets(self):
        for ks in (WIN_LEFT_KEYS, WIN_RIGHT_KEYS, ESC_KEYS, QUIT_KEYS,
                   MARK_IN_KEYS, MARK_OUT_KEYS, WRAP_TOGGLE_KEYS,
                   SPEED_DOWN_KEYS, SPEED_UP_KEYS, ENTER_KEYS):
            assert isinstance(ks, set)


# ---------------------------------------------------------------------------
# ensure_runtime_dirs
# ---------------------------------------------------------------------------

class TestEnsureRuntimeDirs:
    def test_creates_sessions_dir(self, tmp_path: Path):
        sessions = tmp_path / "sessions"
        clips = tmp_path / "clips"
        raw = tmp_path / "raw_clips"
        audio = tmp_path / "audio"

        with (
            patch("fun_time.robot_hand.clipper.paths.SESSIONS_DIR", sessions),
            patch("fun_time.robot_hand.clipper.paths.RAW_CLIPS_DIR", raw),
            patch("fun_time.robot_hand.clipper.paths.CLIPS_DIR", clips),
            patch("fun_time.robot_hand.clipper.paths.AUDIO_DIR", audio),
        ):
            ensure_runtime_dirs()

        assert sessions.is_dir()
        assert clips.is_dir()
        assert raw.is_dir()
        assert audio.is_dir()

    def test_idempotent(self, tmp_path: Path):
        sessions = tmp_path / "sessions"
        clips = tmp_path / "clips"
        raw = tmp_path / "raw_clips"
        audio = tmp_path / "audio"

        with (
            patch("fun_time.robot_hand.clipper.paths.SESSIONS_DIR", sessions),
            patch("fun_time.robot_hand.clipper.paths.RAW_CLIPS_DIR", raw),
            patch("fun_time.robot_hand.clipper.paths.CLIPS_DIR", clips),
            patch("fun_time.robot_hand.clipper.paths.AUDIO_DIR", audio),
        ):
            ensure_runtime_dirs()
            ensure_runtime_dirs()  # Should not raise
