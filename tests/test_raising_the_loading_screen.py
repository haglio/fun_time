from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app_support import loading_screen

from fun_time import raising_the_loading_screen, win32
from fun_time.loading_screen import WINDOW_TITLE
from fun_time.overlay_progress import (
    CANCEL_FILENAME,
    PROGRESS_FILENAME,
    STARTUP_PHASES,
    parse_progress,
)
from fun_time.raising_the_loading_screen import open_the_loading_screen
from fun_time.session_handoff import (
    DESKTOP,
    STARTUP_TIMEOUT_S,
    VR,
    crossing_progress_path,
    drop_transition_screen,
    raise_transition_screen,
    returning_from_a_crossing,
)
from tests.made_up_clocks import SleepDrivenClock


def _opened_loading_screen(state_dir, *, cancelable=True, popen=None):
    with patch("fun_time.raising_the_loading_screen.subprocess.Popen", popen or MagicMock()), \
         patch("fun_time.raising_the_loading_screen.wait_for_window", return_value=0):
        return open_the_loading_screen(state_dir, show_overlays=True, sibling_checkouts="",
                              cancelable=cancelable)


class TestWhatTheLoadingScreenSaysBeforeTheScreenIsUp:
    def test_the_first_phase_is_on_disk_before_the_screen_is_launched(self, tmp_path: Path):
        written: list[str] = []
        popen = MagicMock(side_effect=lambda *_a, **_kw: written.append(
            (tmp_path / PROGRESS_FILENAME).read_text(encoding="utf-8")) or MagicMock())

        _opened_loading_screen(tmp_path, popen=popen)

        assert parse_progress(written[0]).message == STARTUP_PHASES[0].message

    def test_a_done_a_crash_left_there_cannot_close_the_new_loading_screen(self, tmp_path: Path):
        (tmp_path / PROGRESS_FILENAME).write_text("DONE", encoding="utf-8")

        loading_screen = _opened_loading_screen(tmp_path)

        assert not parse_progress(loading_screen.progress_file.read_text(encoding="utf-8")).done


class TestTheLaunchersLoadingScreen:
    @pytest.fixture
    def launched(self, tmp_path, monkeypatch):
        monkeypatch.setenv(loading_screen.FOLDER, str(tmp_path / "screen"))
        monkeypatch.setenv(loading_screen.LINES, '["Updating Fun Time..."]')
        return tmp_path / "screen"

    def test_the_loading_screen_is_told_where_it_meets_the_one_its_launcher_showed(self, tmp_path, launched):
        popen = MagicMock()

        _opened_loading_screen(tmp_path, popen=popen)

        environment = popen.call_args.kwargs["env"]
        assert (environment[loading_screen.FOLDER], environment[loading_screen.LINES]) == (
            str(launched), '["Updating Fun Time..."]')

    def test_nothing_else_the_session_starts_is_told(self, tmp_path, launched):
        _opened_loading_screen(tmp_path)

        assert loading_screen.FOLDER not in os.environ

    def test_a_loading_screen_on_sibling_checkouts_is_told_as_well(self, tmp_path, launched):
        popen = MagicMock()
        with patch("fun_time.raising_the_loading_screen.subprocess.Popen", popen), \
             patch("fun_time.raising_the_loading_screen.wait_for_window", return_value=0):
            open_the_loading_screen(tmp_path, show_overlays=True, sibling_checkouts=str(tmp_path))

        environment = popen.call_args.kwargs["env"]
        assert environment[loading_screen.FOLDER] == str(launched)
        assert environment["PYTHONPATH"].startswith(str(tmp_path))


def test_the_loading_screen_goes_the_moment_the_fun_time_that_raised_it_does(tmp_path: Path):
    screen = MagicMock()

    with patch("fun_time.raising_the_loading_screen.tie_to_this_process") as tie:
        _opened_loading_screen(tmp_path, popen=MagicMock(return_value=screen))

    tie.assert_called_once_with(screen)


class TestTakingTheLoadingScreenDown:
    def test_the_screen_is_told_and_then_waited_for(self, tmp_path: Path):
        loading_screen = _opened_loading_screen(tmp_path)

        loading_screen.take_it_down()

        loading_screen.process.wait.assert_called_once()
        assert not loading_screen.progress_file.exists()

    def test_a_screen_that_will_not_go_is_killed(self, tmp_path: Path):
        loading_screen = _opened_loading_screen(tmp_path)
        loading_screen.process.wait.side_effect = subprocess.TimeoutExpired("loading_screen", 3.0)

        loading_screen.take_it_down()

        loading_screen.process.kill.assert_called_once()

    def test_a_session_with_no_loading_screen_takes_nothing_down(self, tmp_path: Path):
        loading_screen = open_the_loading_screen(tmp_path, show_overlays=False, sibling_checkouts="")

        loading_screen.take_it_down()

        assert not loading_screen.progress_file.exists()


class TestWhatEscCancelsAtTheLoadingScreen:
    @classmethod
    def _opened_line(cls, state_dir, *, cancelable=True):
        loading_screen = _opened_loading_screen(state_dir, cancelable=cancelable)
        loading_screen.progress.advance("services")
        return parse_progress(loading_screen.progress_file.read_text(encoding="utf-8"))

    def test_a_launch_says_esc_cancels_opening_fun_time(self, tmp_path):
        assert self._opened_line(tmp_path).hint == "Press Esc to cancel opening Fun Time"

    def test_a_launch_arriving_from_vr_says_esc_cancels_exiting_vr(self, tmp_path):
        raise_transition_screen(tmp_path, DESKTOP)

        assert self._opened_line(tmp_path).hint == "Press Esc to cancel exiting VR"

    def test_a_launch_on_the_way_back_offers_no_esc(self, tmp_path):
        raise_transition_screen(tmp_path, DESKTOP)

        assert self._opened_line(tmp_path, cancelable=False).hint == ""

    def test_an_esc_pressed_while_the_app_changed_over_calls_the_arrival_off(
        self, tmp_path,
    ):
        raise_transition_screen(tmp_path, DESKTOP)
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        loading_screen = _opened_loading_screen(tmp_path)

        assert loading_screen.progress.cancelled

    def test_the_first_line_is_not_a_checkpoint_the_flag_can_stop(self, tmp_path):
        raise_transition_screen(tmp_path, DESKTOP)
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        loading_screen = _opened_loading_screen(tmp_path)

        assert parse_progress(
            loading_screen.progress_file.read_text(encoding="utf-8")).message == STARTUP_PHASES[0].message

    def test_a_launch_on_the_way_back_clears_the_esc_that_sent_it(self, tmp_path):
        raise_transition_screen(tmp_path, DESKTOP)
        flag = tmp_path / CANCEL_FILENAME
        flag.write_text("cancel\n", encoding="utf-8")

        loading_screen = _opened_loading_screen(tmp_path, cancelable=False)

        assert not flag.exists()
        assert not loading_screen.progress.cancelled


class TestEscOnTheWayBackFromACancelledCrossing:
    def test_the_standing_loading_screen_is_what_says_this_is_a_return(self, tmp_path):
        assert not returning_from_a_crossing(tmp_path)

        raise_transition_screen(tmp_path, VR)
        assert returning_from_a_crossing(tmp_path)

        drop_transition_screen(tmp_path)
        assert returning_from_a_crossing(tmp_path), (
            "DONE is the other session's word that it is up, not a deletion"
        )


@pytest.mark.real_startup_waits
class TestTheWayBackFromVr:
    @pytest.fixture
    def clock(self, monkeypatch) -> SleepDrivenClock:
        clock = SleepDrivenClock()
        monkeypatch.setattr(win32, "time", clock)
        return clock

    @staticmethod
    def _a_screen_seen_from(clock: SleepDrivenClock, seconds: float, monkeypatch) -> None:
        def its_window(_pid, title, *, include_hidden=False) -> int:
            return 77 if title == WINDOW_TITLE and (include_hidden or clock.now >= seconds) else 0

        monkeypatch.setattr(raising_the_loading_screen, "find_window_for_process", its_window)

    @staticmethod
    def _launched(state_dir: Path) -> None:
        with patch("fun_time.raising_the_loading_screen.subprocess.Popen", MagicMock()):
            open_the_loading_screen(state_dir, show_overlays=True, sibling_checkouts="")

    def test_returning_to_fun_time_stays_up_until_a_late_loading_screen_can_be_seen(
            self, tmp_path, clock, monkeypatch):
        raise_transition_screen(tmp_path, DESKTOP)
        seen_from = raising_the_loading_screen.WINDOW_WAIT_S + 3.0
        self._a_screen_seen_from(clock, seen_from, monkeypatch)

        self._launched(tmp_path)

        assert parse_progress(crossing_progress_path(tmp_path).read_text(encoding="utf-8")).done
        assert seen_from <= clock.now < seen_from + 1.0, (
            f"handed over at {clock.now:.1f}s, to a loading screen seen from {seen_from:.1f}s")

    def test_a_launch_with_nothing_to_hand_over_waits_only_the_usual_few_seconds(
            self, tmp_path, clock, monkeypatch):
        self._a_screen_seen_from(clock, raising_the_loading_screen.WINDOW_WAIT_S + 3.0, monkeypatch)

        self._launched(tmp_path)

        assert clock.now == pytest.approx(raising_the_loading_screen.WINDOW_WAIT_S, abs=0.2)

    def test_a_loading_screen_never_seen_holds_the_way_back_only_so_long(
            self, tmp_path, clock, monkeypatch):
        raise_transition_screen(tmp_path, DESKTOP)
        self._a_screen_seen_from(clock, float("inf"), monkeypatch)

        self._launched(tmp_path)

        assert parse_progress(crossing_progress_path(tmp_path).read_text(encoding="utf-8")).done
        assert clock.now == pytest.approx(raising_the_loading_screen.WINDOW_WAIT_UNDER_A_CROSSING_S, abs=0.2)


def test_the_way_back_leaves_fun_time_most_of_the_time_the_crossing_gives_it():
    assert raising_the_loading_screen.WINDOW_WAIT_UNDER_A_CROSSING_S <= STARTUP_TIMEOUT_S / 2
