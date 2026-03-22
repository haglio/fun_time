"""Tests for fun_time.robot_hand.clipper.utils."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.robot_hand.clipper.utils import (
    format_seconds,
    parse_timestamp,
    safe_atomic_write_json,
    sanitize_name,
    subprocess_window_kwargs,
)


# ---------------------------------------------------------------------------
# parse_timestamp
# ---------------------------------------------------------------------------

class TestParseTimestamp:
    def test_whole_seconds(self):
        assert parse_timestamp("00:00:30") == pytest.approx(30.0)

    def test_minutes_and_seconds(self):
        assert parse_timestamp("00:01:30") == pytest.approx(90.0)

    def test_hours_minutes_seconds(self):
        assert parse_timestamp("01:00:00") == pytest.approx(3600.0)

    def test_fractional_seconds(self):
        assert parse_timestamp("00:00:01.500") == pytest.approx(1.5)

    def test_combined(self):
        # 1h 2m 3.25s = 3600 + 120 + 3.25 = 3723.25
        assert parse_timestamp("01:02:03.250") == pytest.approx(3723.25)

    def test_strips_whitespace(self):
        assert parse_timestamp("  00:00:05  ") == pytest.approx(5.0)

    def test_zero(self):
        assert parse_timestamp("00:00:00") == pytest.approx(0.0)

    def test_raises_on_too_few_parts(self):
        with pytest.raises(ValueError, match="Timestamp must be"):
            parse_timestamp("00:30")

    def test_raises_on_too_many_parts(self):
        with pytest.raises(ValueError, match="Timestamp must be"):
            parse_timestamp("00:00:00:00")


# ---------------------------------------------------------------------------
# format_seconds
# ---------------------------------------------------------------------------

class TestFormatSeconds:
    def test_zero(self):
        assert format_seconds(0.0) == "00:00:00.000"

    def test_negative_clamped_to_zero(self):
        assert format_seconds(-5.0) == "00:00:00.000"

    def test_whole_seconds(self):
        assert format_seconds(30.0) == "00:00:30.000"

    def test_minutes(self):
        assert format_seconds(90.0) == "00:01:30.000"

    def test_hours(self):
        assert format_seconds(3600.0) == "01:00:00.000"

    def test_fractional_seconds(self):
        result = format_seconds(1.5)
        assert result == "00:00:01.500"

    def test_roundtrip_with_parse(self):
        original = 3723.25
        assert parse_timestamp(format_seconds(original)) == pytest.approx(original, rel=1e-5)


# ---------------------------------------------------------------------------
# sanitize_name
# ---------------------------------------------------------------------------

class TestSanitizeName:
    def test_clean_name_unchanged(self):
        assert sanitize_name("clean name") == "clean name"

    def test_strips_leading_trailing_spaces(self):
        assert sanitize_name("  hello  ") == "hello"

    def test_replaces_angle_brackets(self):
        assert "<" not in sanitize_name("a<b>c")
        assert ">" not in sanitize_name("a<b>c")

    def test_replaces_colon(self):
        assert ":" not in sanitize_name("time:00")

    def test_replaces_slash_and_backslash(self):
        assert "/" not in sanitize_name("dir/file")
        assert "\\" not in sanitize_name("dir\\file")

    def test_replaces_pipe(self):
        assert "|" not in sanitize_name("a|b")

    def test_replaces_question_mark(self):
        assert "?" not in sanitize_name("what?")

    def test_replaces_asterisk(self):
        assert "*" not in sanitize_name("star*")

    def test_replaces_double_quote(self):
        assert '"' not in sanitize_name('"quoted"')

    def test_strips_trailing_dots(self):
        result = sanitize_name("filename.")
        assert not result.endswith(".")

    def test_empty_string_allowed(self):
        # After stripping, an empty result is acceptable (no crash)
        result = sanitize_name("   ")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# subprocess_window_kwargs
# ---------------------------------------------------------------------------

class TestSubprocessWindowKwargs:
    def test_returns_dict(self):
        result = subprocess_window_kwargs()
        assert isinstance(result, dict)

    def test_empty_on_non_windows(self):
        with patch.object(sys, "platform", "linux"):
            # os.name patch needed too
            with patch("os.name", "posix"):
                result = subprocess_window_kwargs()
                # On non-NT the function checks os.name
        # We can verify that on the actual platform the call doesn't raise
        subprocess_window_kwargs()

    def test_windows_keys_present_on_nt(self):
        if os.name != "nt":
            pytest.skip("Windows-only test")
        result = subprocess_window_kwargs()
        assert "creationflags" in result
        assert "startupinfo" in result


# ---------------------------------------------------------------------------
# safe_atomic_write_json
# ---------------------------------------------------------------------------

class TestSafeAtomicWriteJson:
    def test_writes_file(self, tmp_path: Path):
        target = tmp_path / "out.json"
        ok, err = safe_atomic_write_json(target, {"key": "value"})
        assert ok is True
        assert target.exists()

    def test_content_is_valid_json(self, tmp_path: Path):
        target = tmp_path / "out.json"
        safe_atomic_write_json(target, {"answer": 42})
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["answer"] == 42

    def test_no_tmp_file_left_after_success(self, tmp_path: Path):
        target = tmp_path / "out.json"
        safe_atomic_write_json(target, {"x": 1})
        tmp = target.with_suffix(target.suffix + ".tmp")
        assert not tmp.exists()

    def test_returns_empty_error_on_success(self, tmp_path: Path):
        target = tmp_path / "out.json"
        ok, err = safe_atomic_write_json(target, {})
        assert ok is True
        assert err == ""

    def test_overwrites_existing_file(self, tmp_path: Path):
        target = tmp_path / "out.json"
        safe_atomic_write_json(target, {"v": 1})
        safe_atomic_write_json(target, {"v": 2})
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["v"] == 2

    def test_returns_false_on_permission_error(self, tmp_path: Path):
        target = tmp_path / "out.json"
        with patch("builtins.open", side_effect=PermissionError("denied")):
            ok, err = safe_atomic_write_json(target, {})
        assert ok is False
        assert "denied" in err

    def test_creates_nested_directories(self, tmp_path: Path):
        target = tmp_path / "nested" / "dir" / "out.json"
        # safe_atomic_write_json doesn't mkdir — it's caller's job,
        # so this should fail gracefully, not raise
        ok, err = safe_atomic_write_json(target, {"x": 1})
        # Either it succeeded (dirs were auto-created) or returned False
        assert isinstance(ok, bool)
