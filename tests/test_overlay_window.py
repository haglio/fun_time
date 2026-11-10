from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from app_support import loading_screen as shared_loading_screen
from app_support.loading_screen import Screens
from shared_ui.loading_handover import TakingOver
from shared_ui.loading_panel import LoadingPanel, size
from shared_ui.palette import LOADING_ACCENT

from fun_time import overlay_window, preview_marker
from fun_time.overlay_progress import (
    TORN_OR_NOT_YET_FLUSHED,
    Progress,
    cancel_file_for,
    parse_progress,
    startup_still_building,
)
from fun_time.overlay_window import POLL_MS, TOPMOST_POLL_MS, OverlaySurface, OverlayWindow
from tests.integration.integration_support import environment_with_this_checkouts_siblings


class TestParseProgress:
    def test_parses_step_and_message(self):
        assert parse_progress("3/7|Loading stuff...") == Progress(
            step=3, total=7, message="Loading stuff...")

    def test_parses_done(self):
        assert parse_progress("DONE").done is True

    def test_a_line_written_but_not_yet_flushed_reads_the_same_as_torn(self):
        assert parse_progress("") == parse_progress("3/") == TORN_OR_NOT_YET_FLUSHED

    def test_a_fragment_is_a_line_it_cannot_read(self):
        assert parse_progress("garbage data") == Progress(malformed=True)


class _TheOffscreenRootTkLacks:
    def __init__(self, screen=(2560, 1440)):
        self.destroyed = False
        self.rearmed = []
        self.geometries: list[str] = []
        self._screen = screen
        self.painted = 0

    def destroy(self):
        self.destroyed = True

    def update(self):
        self.painted += 1

    def mainloop(self):
        pass

    def after(self, ms, callback):
        self.rearmed.append((ms, callback))

    def geometry(self, spec):
        self.geometries.append(spec)

    def winfo_screenwidth(self):
        return self._screen[0]

    def winfo_screenheight(self):
        return self._screen[1]


class _FakeCanvas:
    def __init__(self):
        self.shown: list[LoadingPanel] = []
        self.placed: list[tuple[int, int]] = []

    def show(self, panel: LoadingPanel) -> None:
        self.shown.append(panel)

    def place_at(self, x: int, y: int) -> None:
        self.placed.append((x, y))


def _loading_screen(tmp_path: Path, *, stale_timeout_s: float = 5.0,
           title: str = "Fun Time Loading", taking_over: TakingOver | None = None) -> OverlayWindow:
    return OverlayWindow(
        tmp_path / "progress.txt",
        OverlaySurface(_TheOffscreenRootTkLacks(), _FakeCanvas(), title),
        panel=LoadingPanel(wordmark="Fun Time", status="Starting..."),
        stale_timeout_s=stale_timeout_s,
        taking_over=taking_over or TakingOver(None),
    )


class TestTheOnlyTwoWaysDownFromOverEveryMonitor:
    def test_the_done_marker_takes_the_loading_screen_down(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text("DONE", encoding="utf-8")

        window._poll()

        assert window._root.destroyed
        assert window._root.rearmed == []

    def test_a_live_progress_file_keeps_the_loading_screen_up_and_polling(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text("3/7|Launching companions...", encoding="utf-8")

        window._poll()

        assert not window._root.destroyed
        assert window.panel.fraction == 3 / 7
        assert window.panel.status == "Launching companions..."
        assert [ms for ms, _cb in window._root.rearmed] == [POLL_MS]

    def test_the_watchdog_closes_a_loading_screen_whose_orchestrator_died(self, tmp_path: Path):
        window = _loading_screen(tmp_path, stale_timeout_s=5.0)
        window._progress_file.write_text("3/7|Positioning windows...", encoding="utf-8")
        window._poll()
        assert not window._root.destroyed

        gone_quiet = time.time() - 6.0
        os.utime(window._progress_file, (gone_quiet, gone_quiet))

        window._poll()

        assert window._root.destroyed

    def test_a_loading_screen_that_looks_before_its_progress_file_appears_holds_and_keeps_looking(
            self, tmp_path: Path):
        window = _loading_screen(tmp_path, stale_timeout_s=60.0)

        window._poll()

        assert not window._root.destroyed
        assert len(window._root.rearmed) == 1

    def test_a_loading_screen_whose_progress_file_never_appears_comes_down_once_its_wait_is_up(
            self, tmp_path: Path):
        window = _loading_screen(tmp_path, stale_timeout_s=0.05)
        time.sleep(0.1)

        window._poll()

        assert window._root.destroyed

    def test_a_progress_file_the_loading_screen_can_never_read_still_lets_its_wait_run_out(
            self, tmp_path: Path):
        window = _loading_screen(tmp_path, stale_timeout_s=0.05)
        window._progress_file.mkdir()
        time.sleep(0.1)

        window._poll()

        assert window._root.destroyed


def test_the_two_wordmarks_take_their_tone_from_one_place():
    assert overlay_window.opening_panel("Starting...").ink == preview_marker.wordmark_ink(None)
    assert preview_marker.wordmark_ink(None) == LOADING_ACCENT
    assert overlay_window.opening_panel("Starting...").wordmark == preview_marker.APP_TITLE


def test_the_label_holds_the_picture_it_shows():
    label = MagicMock()
    picture = object()

    with patch("fun_time.overlay_window.ImageTk.PhotoImage", return_value=picture):
        overlay_window._LabelCanvas(label).show(LoadingPanel(wordmark="Fun Time", status="Starting..."))

    assert label.image is picture
    label.configure.assert_called_once_with(image=picture)


def test_the_screen_the_launch_waits_on_loads_no_qt():
    loaded = subprocess.run(
        [sys.executable, "-c",
         ("import sys, fun_time.overlay_window\n"
         "print(any(m.startswith('PyQt6') for m in sys.modules))\n")],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent),
        env=environment_with_this_checkouts_siblings(),
        check=False,
    )

    assert loaded.stdout.strip() == "False", loaded.stdout + loaded.stderr


class TestTheLoadingScreenKeepsTheTopOfItsBand:
    def test_the_handle_is_resolved_by_title_once_and_then_reused(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        looked_up: list[tuple] = []

        with patch("fun_time.overlay_window.find_window_by_title",
                   side_effect=lambda *a, **k: (looked_up.append((a, k)), 4242)[1]), \
             patch("fun_time.overlay_window.set_always_on_top") as banded:
            window._stay_on_top()
            window._stay_on_top()

        assert looked_up == [(("Fun Time Loading",), {"exact": True})]
        assert banded.call_args_list == [call(4242, True), call(4242, True)]

    def test_the_top_is_taken_back_through_windows_not_tks_topmost_flag(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._root.attributes = MagicMock()

        with patch("fun_time.overlay_window.find_window_by_title", return_value=4242), \
             patch("fun_time.overlay_window.set_always_on_top") as banded:
            window._stay_on_top()

        window._root.attributes.assert_not_called()
        banded.assert_called_once_with(4242, True)

    def test_nothing_is_banded_before_the_window_can_be_found(self, tmp_path: Path):
        window = _loading_screen(tmp_path)

        with patch("fun_time.overlay_window.find_window_by_title", return_value=0), \
             patch("fun_time.overlay_window.set_always_on_top") as banded:
            window._stay_on_top()

        banded.assert_not_called()

    def test_it_re_arms_itself_at_the_fast_cadence(self, tmp_path: Path):
        window = _loading_screen(tmp_path)

        with patch("fun_time.overlay_window.find_window_by_title", return_value=0):
            window._stay_on_top()

        assert window._root.rearmed[-1][0] == TOPMOST_POLL_MS

    def test_a_window_the_other_timer_took_down_stops_rather_than_raising(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._root.after = MagicMock(side_effect=tk.TclError("destroyed"))

        with patch("fun_time.overlay_window.find_window_by_title", return_value=0):
            window._stay_on_top()


class TestWhatEscWouldCancel:
    def test_the_words_under_the_bar_are_the_lines_own(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text(
            "1/6|Preparing services...|Press Esc to cancel opening Fun Time", encoding="utf-8")

        window._poll()

        assert window.panel.hint == "Press Esc to cancel opening Fun Time"

    def test_an_esc_only_the_hotkey_script_heard_still_turns_it_to_canceling(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text(
            "1/6|Preparing services...|Press Esc to cancel opening Fun Time", encoding="utf-8")
        cancel_file_for(window._progress_file).write_text("cancel\n", encoding="utf-8")

        window._poll()

        assert window.panel.status == "Canceling..."
        assert window.panel.hint == ""

    def test_the_quit_chord_never_turns_it_to_canceling(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text(
            "1/4|Closing...|Press Esc to cancel closing Fun Time", encoding="utf-8")
        cancel_file_for(window._progress_file).write_text("quit\n", encoding="utf-8")

        window._poll()

        assert window.panel.status == "Closing..."
        assert window.panel.hint == "Press Esc to cancel closing Fun Time"

    def test_a_loading_screen_offering_nothing_goes_on_showing_its_own_words(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text("2/6|Launching companions...", encoding="utf-8")
        cancel_file_for(window._progress_file).write_text("cancel\n", encoding="utf-8")

        window._poll()

        assert window.panel.status == "Launching companions..."
        assert window.panel.hint == ""

    def test_an_esc_too_early_for_the_hotkey_script_drops_the_flag_itself(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text(
            "1/6|Preparing services...|Press Esc to cancel opening Fun Time", encoding="utf-8")
        window._poll()

        window._on_escape()

        assert cancel_file_for(window._progress_file).exists()
        assert window.panel.status == "Canceling..."
        assert window.panel.hint == ""

    def test_a_second_esc_asks_nothing_more(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text(
            "1/6|Preparing services...|Press Esc to cancel opening Fun Time", encoding="utf-8")
        window._poll()
        window._on_escape()
        cancel_file_for(window._progress_file).unlink()

        window._on_escape()

        assert not cancel_file_for(window._progress_file).exists()

    def test_canceling_holds_against_a_phase_written_just_before_the_esc(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text(
            "1/6|Preparing services...|Press Esc to cancel opening Fun Time", encoding="utf-8")
        window._poll()
        window._on_escape()

        window._progress_file.write_text("2/6|Launching companions...", encoding="utf-8")
        window._poll()

        assert window.panel.status == "Canceling..."
        assert window.panel.hint == ""

    def test_esc_on_a_loading_screen_offering_nothing_does_nothing(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text("2/4|Closing Funestras...", encoding="utf-8")
        window._poll()

        window._on_escape()

        assert not cancel_file_for(window._progress_file).exists()
        assert window.panel.status == "Closing Funestras..."

    def test_esc_never_uncovers_the_fun_time_the_screen_is_hiding(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        for line in ("1/6|Preparing services...|Press Esc to cancel opening Fun Time",
                     "2/4|Closing Funestras..."):
            window._progress_file.write_text(line, encoding="utf-8")
            window._poll()
            window._on_escape()

        assert not window._root.destroyed


class TestALineTheLoadingScreenCannotRead:
    def test_a_line_torn_before_its_total_never_reads_as_zero_percent(self, tmp_path: Path):
        window = _loading_screen(tmp_path)
        window._progress_file.write_text("3/6|Waiting for Funestras...", encoding="utf-8")
        window._poll()
        assert window.panel.fraction == 0.5

        window._progress_file.write_text("3/", encoding="utf-8")
        window._poll()

        assert window.panel.fraction == 0.5
        assert window.panel.status == "Waiting for Funestras..."

    def test_a_line_torn_inside_a_character_leaves_the_way_down_polling(self, tmp_path: Path):
        window = _loading_screen(tmp_path, stale_timeout_s=60.0)
        window._progress_file.write_bytes("2/6|Waiting for Funestras…".encode()[:-1])

        window._poll()

        assert not window._root.destroyed
        assert [ms for ms, _cb in window._root.rearmed] == [POLL_MS]

    def test_a_torn_line_is_not_the_end_of_startup(self, tmp_path: Path):
        (tmp_path / "startup_progress.txt").write_text("3/", encoding="utf-8")

        assert startup_still_building(tmp_path) is True

    def test_a_line_it_can_read_says_so(self, tmp_path: Path):
        assert parse_progress("3/6|Positioning windows...") == Progress(
            step=3, total=6, message="Positioning windows...", done=False)
        assert parse_progress("DONE").done is True
        assert parse_progress("nonsense").malformed is True
        assert parse_progress("").malformed is True
class TestTheLoadingScreenFollowsTheMonitorsItCovers:
    AS_FOUND_AT_STARTUP = Screens(virtual=(0, 0, 2560, 1440), primary=(2560, 1440), pointer_work_area=(0, 0, 2560, 1410))
    AS_THE_HEADSETS_RUNTIME_LEFT_THEM = Screens(virtual=(-1920, 0, 4480, 1440), primary=(2560, 1440), pointer_work_area=(0, 0, 2560, 1410))

    def _covering(self, tmp_path: Path, measured):
        loading_screen = _loading_screen(tmp_path)
        (tmp_path / "progress.txt").write_text("1/5|Starting...", encoding="utf-8")
        with patch("fun_time.overlay_window.screens", side_effect=measured):
            for _ in measured:
                loading_screen._poll()
        return loading_screen

    def test_displays_the_headsets_runtime_rearranges_are_covered_again(self, tmp_path: Path):
        loading_screen = self._covering(tmp_path, [self.AS_FOUND_AT_STARTUP, self.AS_THE_HEADSETS_RUNTIME_LEFT_THEM])

        assert loading_screen._root.geometries == ["2560x1440+0+0", "4480x1440+-1920+0"]

    def test_a_display_set_that_has_not_moved_is_left_alone(self, tmp_path: Path):
        moved_the_pointer = replace(self.AS_FOUND_AT_STARTUP, pointer_work_area=(2560, 0, 1440, 3410))

        loading_screen = self._covering(tmp_path, [self.AS_FOUND_AT_STARTUP, moved_the_pointer, self.AS_FOUND_AT_STARTUP])

        assert loading_screen._root.geometries == ["2560x1440+0+0"]

    def test_the_panel_is_centered_again_on_the_main_monitor_of_the_new_set(self, tmp_path: Path):
        width, height = size()

        loading_screen = self._covering(tmp_path, [self.AS_FOUND_AT_STARTUP, self.AS_THE_HEADSETS_RUNTIME_LEFT_THEM])

        on_its_middle = (1280 - width // 2, 720 - height // 2)
        assert loading_screen._canvas.placed == [on_its_middle, (on_its_middle[0] + 1920, on_its_middle[1])]

    def test_screens_windows_cannot_measure_are_covered_as_far_as_tk_sees(self, tmp_path: Path):
        loading_screen = self._covering(tmp_path, [None])

        assert loading_screen._root.geometries == ["2560x1440+0+0"]


class TestTakingOverFromTheLaunchersLoadingScreen:
    @pytest.fixture
    def place(self, tmp_path):
        return shared_loading_screen.MeetingPlace(tmp_path / "screen", ("Updating Fun Time...",))

    def test_its_bar_carries_on_from_where_the_launchers_screen_got(self, tmp_path: Path, place):
        place.stand(shared_loading_screen.Shown(left=0, top=0, fraction=0.5))
        loading_screen = _loading_screen(tmp_path, taking_over=TakingOver(place))
        loading_screen._progress_file.write_text("3/6|Launching companions...", encoding="utf-8")

        loading_screen._poll()

        assert loading_screen.panel.fraction == 0.75

    def test_its_first_frame_already_says_what_the_launch_wrote(self, tmp_path: Path):
        loading_screen = _loading_screen(tmp_path)
        loading_screen._progress_file.write_text("0/6|Starting...|Press Esc to cancel opening Fun Time", encoding="utf-8")

        loading_screen.run()

        assert loading_screen._canvas.shown[-1].hint == "Press Esc to cancel opening Fun Time"
        assert loading_screen._root.painted == 1

    def test_the_screen_reports_itself_shown_only_after_its_first_paint(self, tmp_path: Path):
        loading_screen = _loading_screen(tmp_path)
        painted_when_shown: list[int] = []

        loading_screen.run(on_shown=lambda: painted_when_shown.append(loading_screen._root.painted))

        assert painted_when_shown == [1]

    def test_once_up_it_tells_the_launchers_screen_to_go_and_leaves_it_this_panel(self, tmp_path: Path, place):
        place.stand(shared_loading_screen.Shown(left=0, top=0, fraction=0.5))
        loading_screen = _loading_screen(tmp_path, taking_over=TakingOver(place))
        loading_screen._progress_file.write_text("0/6|Starting...|Press Esc to cancel opening Fun Time", encoding="utf-8")

        loading_screen.run()

        assert place.taken
        assert place.snapshots().covers_every_monitor
