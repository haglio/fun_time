from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from fun_time.overlay_progress import Progress, parse_progress
from fun_time.overlay_window import (
    POLL_MS,
    OverlayWindow,
    _Content,
)


class TestParseProgress:
    def test_parses_step_and_message(self):
        assert parse_progress("3/7|Loading stuff...") == Progress(
            step=3, total=7, message="Loading stuff...")

    def test_parses_done(self):
        assert parse_progress("DONE").done is True

    def test_an_empty_file_is_a_line_it_cannot_read(self):
        """Written but not yet flushed reads the same as torn, and neither is
        a step: both leave the bar where the last readable line put it."""
        assert parse_progress("") == Progress(malformed=True)

    def test_so_is_a_fragment(self):
        assert parse_progress("garbage data") == Progress(malformed=True)


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


def _cover(tmp_path: Path, *, stale_timeout_s: float = 5.0,
           title: str = "Fun Time Loading") -> OverlayWindow:
    """The overlay's live loops over fakes standing in for Tk.

    Constructing the real window opens a borderless cover over every monitor
    of whoever runs the suite — the one thing this conftest exists to prevent
    — and unlike Qt, tkinter has no offscreen platform.  So the Tk widgets
    are the boundary faked here, and everything from the progress file to the
    destroy decision runs for real.

    Every attribute the constructor sets, so the topmost pass and the cancel
    are reachable too; they were left out and could not be called at all.
    """
    window = OverlayWindow.__new__(OverlayWindow)
    window._progress_file = tmp_path / "progress.txt"
    window._stale_timeout_s = stale_timeout_s
    window._last_modified = 0.0
    window._status_held = False
    window._offering = False
    window._title = title
    window._hwnd = 0
    window._root = _FakeRoot()
    window._content = _Content(
        status_label=_FakeLabel(),
        progress_var=_FakeVar(),
        hint_label=_FakeLabel(),
    )
    return window


class TestTheCoverComesDown:
    """The full-screen cover has exactly two ways down, and a regression in
    either leaves the user's whole desktop under an opaque window with no
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
        assert window._content.progress_var.value == 3 / 7 * 100
        assert window._content.status_label.text == "Launching companions..."
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


def test_the_two_wordmarks_are_one_magenta():
    """The panel's "Fun Time" and the cover's are the same tone.  They were two
    hex literals in two files kept in step by a comment, in a repo where one of
    the files cannot import Qt and the other cannot import tkinter."""
    from PyQt6.QtGui import QColor

    from fun_time.cover_palette import WORDMARK_MAGENTA
    from fun_time.dashboard_app import COLOR_APP_TITLE

    assert QColor(WORDMARK_MAGENTA) == COLOR_APP_TITLE


def test_a_cover_process_loads_no_qt():
    """A cover's whole job is to be on screen fast, and the orchestrator waits
    on its window before it goes on.  Taking the palette or the face from
    shared_ui would put PyQt6 on that path for five strings."""
    import subprocess

    loaded = subprocess.run(
        [sys.executable, "-c",
         "import sys, fun_time.overlay_window\n"
         "print(any(m.startswith('PyQt6') for m in sys.modules))\n"],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
    )

    assert loaded.stdout.strip() == "False", loaded.stdout + loaded.stderr


class TestTheCoverKeepsTheTopOfItsBand:
    """Nothing keeps a topmost window above the OTHER topmost windows: every
    window a session raises lands over this one, and Windows never says so.
    How fast this runs IS how long a player shows through the scrim."""

    def test_the_handle_is_resolved_by_title_once_and_then_reused(self, tmp_path: Path):
        window = _cover(tmp_path)
        looked_up: list[tuple] = []

        with patch("fun_time.overlay_window.find_window_by_title",
                   side_effect=lambda *a, **k: (looked_up.append((a, k)), 4242)[1]), \
             patch("fun_time.overlay_window.set_always_on_top") as banded:
            window._stay_on_top()
            window._stay_on_top()

        assert looked_up == [(("Fun Time Loading",), {"exact": True})]
        assert banded.call_args_list == [call(4242, True), call(4242, True)]

    def test_nothing_is_banded_before_the_window_can_be_found(self, tmp_path: Path):
        """Brand new, it is at the top of the band by construction; there is
        nothing over it to fix yet."""
        window = _cover(tmp_path)

        with patch("fun_time.overlay_window.find_window_by_title", return_value=0), \
             patch("fun_time.overlay_window.set_always_on_top") as banded:
            window._stay_on_top()

        banded.assert_not_called()

    def test_it_re_arms_itself_at_the_fast_cadence(self, tmp_path: Path):
        from fun_time.overlay_window import TOPMOST_POLL_MS

        window = _cover(tmp_path)

        with patch("fun_time.overlay_window.find_window_by_title", return_value=0):
            window._stay_on_top()

        assert window._root.rearmed[-1][0] == TOPMOST_POLL_MS

    def test_a_destroyed_window_stops_rather_than_raising(self, tmp_path: Path):
        """The cover comes down on its own timer; the two are not synchronised."""
        window = _cover(tmp_path)
        window._root.after = MagicMock(side_effect=tk.TclError("destroyed"))

        with patch("fun_time.overlay_window.find_window_by_title", return_value=0):
            window._stay_on_top()  # must not raise


class TestWhatEscWouldCancel:
    def test_the_words_under_the_bar_are_the_lines_own(self, tmp_path: Path):
        window = _cover(tmp_path)
        window._progress_file.write_text(
            "1/6|Preparing services...|Press Esc to cancel opening Fun Time", encoding="utf-8")

        window._poll()

        assert window._content.hint_label.text == "Press Esc to cancel opening Fun Time"

    def test_while_it_offers_esc_the_flag_turns_it_to_canceling(self, tmp_path: Path):
        """The hotkey script drops the flag without any key reaching this window."""
        from fun_time.overlay_progress import cancel_file_for

        window = _cover(tmp_path)
        window._progress_file.write_text(
            "1/6|Preparing services...|Press Esc to cancel opening Fun Time", encoding="utf-8")
        cancel_file_for(window._progress_file).write_text("cancel\n", encoding="utf-8")

        window._poll()

        assert window._content.status_label.text == "Canceling..."
        assert window._content.hint_label.text == ""

    def test_the_quit_chord_never_turns_it_to_canceling(self, tmp_path: Path):
        """Over the closing screen it calls nothing off: the quit goes on."""
        from fun_time.overlay_progress import cancel_file_for

        window = _cover(tmp_path)
        window._progress_file.write_text(
            "1/4|Closing...|Press Esc to cancel closing Fun Time", encoding="utf-8")
        cancel_file_for(window._progress_file).write_text("quit\n", encoding="utf-8")

        window._poll()

        assert window._content.status_label.text == "Closing..."
        assert window._content.hint_label.text == "Press Esc to cancel closing Fun Time"

    def test_a_cover_offering_nothing_goes_on_showing_its_own_words(self, tmp_path: Path):
        """The way back after an Esc offers no second one, and the flag that
        started it can still be lying there."""
        from fun_time.overlay_progress import cancel_file_for

        window = _cover(tmp_path)
        window._progress_file.write_text("2/6|Launching companions...", encoding="utf-8")
        cancel_file_for(window._progress_file).write_text("cancel\n", encoding="utf-8")

        window._poll()

        assert window._content.status_label.text == "Launching companions..."
        assert window._content.hint_label.text == ""

    def test_esc_on_a_cover_offering_it_drops_the_flag_itself(self, tmp_path: Path):
        """The route that needs the focus, for the moment before the hotkey
        script is up to take the key."""
        from fun_time.overlay_progress import cancel_file_for

        window = _cover(tmp_path)
        window._progress_file.write_text(
            "1/6|Preparing services...|Press Esc to cancel opening Fun Time", encoding="utf-8")
        window._poll()

        window._on_escape()

        assert cancel_file_for(window._progress_file).exists()
        assert window._content.status_label.text == "Canceling..."
        assert window._content.hint_label.text == ""

    def test_a_second_esc_asks_nothing_more(self, tmp_path: Path):
        from fun_time.overlay_progress import cancel_file_for

        window = _cover(tmp_path)
        window._progress_file.write_text(
            "1/6|Preparing services...|Press Esc to cancel opening Fun Time", encoding="utf-8")
        window._poll()
        window._on_escape()
        cancel_file_for(window._progress_file).unlink()

        window._on_escape()

        assert not cancel_file_for(window._progress_file).exists()

    def test_the_words_hold_against_a_phase_still_in_flight(self, tmp_path: Path):
        """A phase written just before the cancel would otherwise flip the line
        back to business as usual while the teardown runs."""
        window = _cover(tmp_path)
        window._progress_file.write_text(
            "1/6|Preparing services...|Press Esc to cancel opening Fun Time", encoding="utf-8")
        window._poll()
        window._on_escape()

        window._progress_file.write_text("2/6|Launching companions...", encoding="utf-8")
        window._poll()

        assert window._content.status_label.text == "Canceling..."
        assert window._content.hint_label.text == ""

    def test_esc_on_a_cover_offering_nothing_does_nothing(self, tmp_path: Path):
        from fun_time.overlay_progress import cancel_file_for

        window = _cover(tmp_path)
        window._progress_file.write_text("2/4|Closing players...", encoding="utf-8")
        window._poll()

        window._on_escape()

        assert not cancel_file_for(window._progress_file).exists()
        assert window._content.status_label.text == "Closing players..."

    def test_esc_never_takes_a_cover_down(self, tmp_path: Path):
        """The cover is there to hide the room while it changes shape; a key
        that uncovered it was never wanted."""
        window = _cover(tmp_path)
        for line in ("1/6|Preparing services...|Press Esc to cancel opening Fun Time",
                     "2/4|Closing players..."):
            window._progress_file.write_text(line, encoding="utf-8")
            window._poll()
            window._on_escape()

        assert not window._root.destroyed


class TestALineTheCoverCannotRead:
    """A torn write leaves a fragment.  Parsed as a 4-tuple, that read back as
    "zero percent done" — indistinguishable from a genuine first phase — and
    the bar snapped to the left in front of the user."""

    def test_the_bar_holds_where_it_was(self, tmp_path: Path):
        """A read that catches the write before its total is a prefix like
        "3/" — parsed as a 4-tuple that came back as step 0 of 1."""
        window = _cover(tmp_path)
        window._progress_file.write_text("3/6|Waiting for players...", encoding="utf-8")
        window._poll()
        assert window._content.progress_var.value == 50.0

        window._progress_file.write_text("3/", encoding="utf-8")
        window._poll()

        assert window._content.progress_var.value == 50.0
        assert window._content.status_label.text == "Waiting for players..."

    def test_a_torn_line_is_not_the_end_of_startup(self, tmp_path: Path):
        """The one thing that lifts the cover is the orchestrator's own DONE."""
        from fun_time.overlay_progress import startup_still_building

        (tmp_path / "startup_progress.txt").write_text("3/", encoding="utf-8")

        assert startup_still_building(tmp_path) is True

    def test_a_line_it_can_read_says_so(self, tmp_path: Path):
        from fun_time.overlay_progress import parse_progress

        assert parse_progress("3/6|Positioning windows...") == Progress(
            step=3, total=6, message="Positioning windows...", done=False)
        assert parse_progress("DONE").done is True
        assert parse_progress("nonsense").malformed is True
        assert parse_progress("").malformed is True
