from __future__ import annotations

import os
import time
from pathlib import Path

from fun_time.overlay_progress import parse_progress
from fun_time.overlay_window import OverlayWindow, POLL_MS, load_icon_image

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


class _FakeRoot:
    def __init__(self):
        self.destroyed = False
        self.rearmed = []

    def destroy(self):
        self.destroyed = True

    def after(self, ms, callback):
        self.rearmed.append((ms, callback))


class _FakeVar:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class _FakeLabel:
    def __init__(self):
        self.text = None

    def configure(self, **kwargs):
        self.text = kwargs.get("text", self.text)


def _cover(tmp_path: Path, *, stale_timeout_s: float = 5.0) -> OverlayWindow:
    """The overlay's poll loop over fakes standing in for Tk.

    Constructing the real window opens a borderless cover over every monitor
    of whoever runs the suite — the one thing this conftest exists to prevent
    — and unlike Qt, tkinter has no offscreen platform.  So the Tk widgets
    are the boundary faked here, and everything from the progress file to the
    destroy decision runs for real.
    """
    window = OverlayWindow.__new__(OverlayWindow)
    window._progress_file = tmp_path / "progress.txt"
    window._stale_timeout_s = stale_timeout_s
    window._cancel = None
    window._last_modified = 0.0
    window._status_held = False
    window._root = _FakeRoot()
    window._progress_var = _FakeVar()
    window._status_label = _FakeLabel()
    window._hint_label = _FakeLabel()
    return window


class TestTheCoverComesDown:
    """The full-screen cover has exactly two ways down, and a regression in
    either leaves the user's whole desktop behind an opaque window with no
    way to dismiss it."""

    def test_the_done_marker_takes_the_cover_down(self, tmp_path: Path):
        window = _cover(tmp_path)
        window._progress_file.write_text("DONE", encoding="utf-8")

        window._poll()

        assert window._root.destroyed
        assert window._root.rearmed == []  # nothing left polling a dead window

    def test_a_live_progress_file_keeps_the_cover_up_and_polling(self, tmp_path: Path):
        window = _cover(tmp_path)
        window._progress_file.write_text("3/7|Launching companions...", encoding="utf-8")

        window._poll()

        assert not window._root.destroyed
        assert window._progress_var.value == 3 / 7 * 100
        assert window._status_label.text == "Launching companions..."
        assert [ms for ms, _cb in window._root.rearmed] == [POLL_MS]

    def test_the_watchdog_closes_a_cover_whose_orchestrator_died(self, tmp_path: Path):
        """No DONE is coming from a dead orchestrator; once the progress file
        has sat unchanged past the staleness budget the cover takes itself
        down rather than hold the desktop forever."""
        window = _cover(tmp_path, stale_timeout_s=5.0)
        window._progress_file.write_text("3/7|Positioning windows...", encoding="utf-8")
        window._poll()  # a healthy poll records the file's mtime
        assert not window._root.destroyed

        gone_quiet = time.time() - 6.0  # unchanged past the staleness budget
        os.utime(window._progress_file, (gone_quiet, gone_quiet))

        window._poll()

        assert window._root.destroyed

    def test_a_missing_progress_file_alone_never_closes_the_cover(self, tmp_path: Path):
        """Before the orchestrator's first write there is nothing to be stale:
        the cover holds, and keeps polling for the file to appear."""
        window = _cover(tmp_path)

        window._poll()

        assert not window._root.destroyed
        assert len(window._root.rearmed) == 1
