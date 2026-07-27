from __future__ import annotations

from pathlib import Path

from fun_time.overlay_window import load_icon_image, parse_progress

ICON_PATH = Path(__file__).resolve().parent.parent / "icon.ico"


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


class TestLoadIconImage:
    def test_loads_icon_at_requested_size(self):
        img = load_icon_image(ICON_PATH, 128)
        assert img.size == (128, 128)

    def test_returns_none_for_missing_file(self):
        result = load_icon_image(Path("nonexistent.ico"), 128)
        assert result is None
