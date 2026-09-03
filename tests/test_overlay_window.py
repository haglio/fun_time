from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from unittest.mock import MagicMock, call, patch
from pathlib import Path

from fun_time.overlay_progress import Progress, parse_progress
from fun_time.overlay_window import (
    POLL_MS,
    OverlayWindow,
    _Content,
    load_icon_image,
)

ICON_PATH = Path(__file__).resolve().parent.parent / "icon.ico"


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


def _cover(tmp_path: Path, *, stale_timeout_s: float = 5.0, cancel=None,
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
    window._cancel = cancel
    window._last_modified = 0.0
    window._status_held = False
    window._title = title
    window._hwnd = 0
    window._root = _FakeRoot()
    window._content = _Content(
        status_label=_FakeLabel(),
        progress_var=_FakeVar(),
        hint_label=_FakeLabel(),
    )
    return window


def _cancel_option(**overrides):
    """A CancelOption whose two callables record what was asked of them."""
    from fun_time.overlay_window import CancelOption

    asked: list[str] = []
    fields = dict(
        hint="Press Esc to cancel",
        pending="Cancelling...",
        request=lambda: asked.append("request"),
        requested=lambda: False,
    )
    fields.update(overrides)
    option = CancelOption(**fields)
    return option, asked


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


def test_the_two_wordmarks_are_one_pink():
    """The panel's "Fun Time" and the cover's are the same tone.  They were two
    hex literals in two files kept in step by a comment, in a repo where one of
    the files cannot import Qt and the other cannot import tkinter."""
    from PyQt6.QtGui import QColor

    from fun_time.cover_palette import WORDMARK_PINK
    from fun_time.dashboard_app import COLOR_APP_TITLE

    assert COLOR_APP_TITLE == QColor(WORDMARK_PINK)


def test_a_cover_process_loads_no_qt():
    """A cover's whole job is to be on screen fast, and the orchestrator waits
    on its window before it goes on.  Taking the palette or the face from
    shared_ui would put PyQt6 on that path for five strings."""
    import subprocess
    import sys

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


class TestTheWayOutStartupOffers:
    """Startup's cover can be called off; shutdown's cannot, and that is the
    only difference between the two."""

    def test_escape_asks_the_orchestrator_to_stop_and_says_so(self, tmp_path: Path):
        cancel, asked = _cancel_option()
        window = _cover(tmp_path, cancel=cancel)

        window._on_escape()

        assert asked == ["request"]
        assert window._content.status_label.text == "Cancelling..."
        assert window._content.hint_label.text == ""

    def test_a_second_escape_asks_nothing_more(self, tmp_path: Path):
        cancel, asked = _cancel_option()
        window = _cover(tmp_path, cancel=cancel)

        window._on_escape()
        window._on_escape()

        assert asked == ["request"]

    def test_the_words_hold_against_a_step_message_still_in_flight(self, tmp_path: Path):
        """A phase written just before the cancel would otherwise flip the line
        back to business as usual while the teardown runs."""
        cancel, _asked = _cancel_option()
        window = _cover(tmp_path, cancel=cancel)
        window._on_escape()

        window._progress_file.write_text("2/6|Launching companions...", encoding="utf-8")
        window._poll()

        assert window._content.status_label.text == "Cancelling..."

    def test_a_cancel_the_hotkey_script_asked_for_is_picked_up_here(self, tmp_path: Path):
        """Esc reaches the orchestrator two ways, and the global hook is the one
        that works when something else has the focus — no key ever reaches this
        window, so the flag on disk is what the words follow."""
        cancel, _asked = _cancel_option(requested=lambda: True)
        window = _cover(tmp_path, cancel=cancel)
        window._progress_file.write_text("1/6|Preparing services...", encoding="utf-8")

        window._poll()

        assert window._content.status_label.text == "Cancelling..."

    def test_a_cover_with_no_way_out_answers_escape_with_nothing(self, tmp_path: Path):
        """Shutdown's, which also never takes the keyboard focus."""
        window = _cover(tmp_path)

        window._on_escape()

        assert window._status_held is False
        assert window._content.status_label.text is None


class TestLoadingTheIcon:
    """The three ways this can fail, each of which used to look identical to a
    working cover.  Narrowed from `except Exception`, so a failure this does not
    expect now reaches the log instead of being read as "no icon"."""

    def test_a_file_that_is_not_an_image_is_no_icon(self, tmp_path: Path):
        """PIL's UnidentifiedImageError is an OSError."""
        not_an_image = tmp_path / "notes.ico"
        not_an_image.write_text("this is not an icon", encoding="utf-8")

        assert load_icon_image(not_an_image, 128) is None

    def test_a_run_without_pillow_is_no_icon(self):
        """The covers are the one part of the session that needs Pillow, and a
        run without it must still put the scrim up."""
        with patch.dict(sys.modules, {"PIL": None}):
            assert load_icon_image(ICON_PATH, 128) is None

    def test_the_icon_is_resized_to_what_was_asked_for(self):
        assert load_icon_image(ICON_PATH, 64).size == (64, 64)


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
