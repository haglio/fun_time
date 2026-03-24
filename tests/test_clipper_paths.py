"""Tests for fun_time.robot_hand.clipper.paths."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.robot_hand.clipper.paths import (
    ACCEPT_SUGGESTED_IN_KEYS,
    ACCEPT_SUGGESTED_OUT_KEYS,
    AUDIO_DIR,
    BOUNDS_CONTRACT_LEFT_KEYS,
    BOUNDS_CONTRACT_RIGHT_KEYS,
    BOUNDS_EXTEND_LEFT_KEYS,
    BOUNDS_EXTEND_RIGHT_KEYS,
    CLIP_POSTPROCESS_SCRIPT,
    CLIPS_DIR,
    ENTER_KEYS,
    ESC_KEYS,
    LAST_SESSION_FILE,
    LOOP_MODE_CYCLE_KEYS,
    LOOP_FIX_SCRIPT,
    MARK_IN_KEYS,
    MARK_OUT_KEYS,
    MODULE_DIR,
    PLAY_PAUSE_KEYS,
    QUIT_KEYS,
    RAW_CLIPS_DIR,
    ROBOT_HAND_DIR,
    SESSIONS_DIR,
    SHIFT_RANGE_LEFT_KEYS,
    SHIFT_RANGE_RIGHT_KEYS,
    SPEED_DOWN_KEYS,
    SPEED_UP_KEYS,
    TAB_KEYS,
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

    def test_clip_postprocess_script_under_module_dir(self):
        assert CLIP_POSTPROCESS_SCRIPT.parent == MODULE_DIR

    def test_clip_postprocess_script_is_python_file(self):
        assert CLIP_POSTPROCESS_SCRIPT.suffix == ".py"

    def test_legacy_loop_fix_script_alias_matches_canonical_path(self):
        assert LOOP_FIX_SCRIPT == CLIP_POSTPROCESS_SCRIPT

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

    def test_bounds_extend_left_contains_a(self):
        assert ord("a") in BOUNDS_EXTEND_LEFT_KEYS
        assert ord("A") in BOUNDS_EXTEND_LEFT_KEYS

    def test_bounds_contract_left_contains_s(self):
        assert ord("s") in BOUNDS_CONTRACT_LEFT_KEYS
        assert ord("S") in BOUNDS_CONTRACT_LEFT_KEYS

    def test_bounds_contract_right_contains_d(self):
        assert ord("d") in BOUNDS_CONTRACT_RIGHT_KEYS
        assert ord("D") in BOUNDS_CONTRACT_RIGHT_KEYS

    def test_bounds_extend_right_contains_f(self):
        assert ord("f") in BOUNDS_EXTEND_RIGHT_KEYS
        assert ord("F") in BOUNDS_EXTEND_RIGHT_KEYS

    def test_wrap_toggle_contains_w(self):
        assert ord("w") in WRAP_TOGGLE_KEYS
        assert ord("W") in WRAP_TOGGLE_KEYS

    def test_loop_mode_cycle_contains_l(self):
        assert ord("l") in LOOP_MODE_CYCLE_KEYS
        assert ord("L") in LOOP_MODE_CYCLE_KEYS

    def test_accept_suggested_in_contains_9(self):
        assert ord("9") in ACCEPT_SUGGESTED_IN_KEYS

    def test_accept_suggested_out_contains_0(self):
        assert ord("0") in ACCEPT_SUGGESTED_OUT_KEYS

    def test_shift_range_left_contains_comma(self):
        assert ord(",") in SHIFT_RANGE_LEFT_KEYS

    def test_shift_range_right_contains_period(self):
        assert ord(".") in SHIFT_RANGE_RIGHT_KEYS

    def test_speed_down_contains_minus(self):
        assert ord("-") in SPEED_DOWN_KEYS

    def test_speed_up_contains_plus(self):
        assert ord("+") in SPEED_UP_KEYS

    def test_play_pause_contains_space(self):
        assert 32 in PLAY_PAUSE_KEYS

    def test_enter_keys_contains_13(self):
        assert 13 in ENTER_KEYS

    def test_tab_keys_contains_9(self):
        assert 9 in TAB_KEYS

    def test_win_left_keys_nonempty(self):
        assert len(WIN_LEFT_KEYS) > 0

    def test_win_right_keys_nonempty(self):
        assert len(WIN_RIGHT_KEYS) > 0

    def test_all_key_sets_are_sets(self):
        for ks in (WIN_LEFT_KEYS, WIN_RIGHT_KEYS, ESC_KEYS, QUIT_KEYS,
                   BOUNDS_EXTEND_LEFT_KEYS, BOUNDS_CONTRACT_LEFT_KEYS, BOUNDS_CONTRACT_RIGHT_KEYS, BOUNDS_EXTEND_RIGHT_KEYS,
                   MARK_IN_KEYS, MARK_OUT_KEYS, WRAP_TOGGLE_KEYS, LOOP_MODE_CYCLE_KEYS,
                   ACCEPT_SUGGESTED_IN_KEYS, ACCEPT_SUGGESTED_OUT_KEYS,
                   SHIFT_RANGE_LEFT_KEYS, SHIFT_RANGE_RIGHT_KEYS,
                   PLAY_PAUSE_KEYS, SPEED_DOWN_KEYS, SPEED_UP_KEYS, ENTER_KEYS, TAB_KEYS):
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
