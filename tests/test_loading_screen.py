from __future__ import annotations

from pathlib import Path

from fun_time.loading_screen import parse_progress


class TestParseProgress:
    def test_parses_step_and_message(self):
        step, total, message, done = parse_progress("3/7|Loading stuff...")
        assert step == 3
        assert total == 7
        assert message == "Loading stuff..."
        assert done is False

    def test_parses_done(self):
        step, total, message, done = parse_progress("DONE")
        assert done is True

    def test_returns_defaults_on_empty(self):
        step, total, message, done = parse_progress("")
        assert step == 0
        assert total == 1
        assert message == ""
        assert done is False

    def test_returns_defaults_on_malformed(self):
        step, total, message, done = parse_progress("garbage data")
        assert step == 0
        assert total == 1
        assert done is False
