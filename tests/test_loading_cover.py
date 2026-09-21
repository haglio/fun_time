from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from fun_time.loading_cover import open_the_cover
from fun_time.overlay_progress import (
    CANCEL_FILENAME,
    PROGRESS_FILENAME,
    STARTUP_PHASES,
    parse_progress,
)
from fun_time.session_handoff import (
    DESKTOP,
    VR,
    drop_crossing_cover,
    raise_crossing_cover,
    returning_from_a_crossing,
)


def _opened_cover(state_dir, *, cancelable=True, popen=None):
    with patch("fun_time.loading_cover.subprocess.Popen", popen or MagicMock()), \
         patch("fun_time.loading_cover.wait_for_window_by_title", return_value=0):
        return open_the_cover(state_dir, show_overlays=True, project_dirs="",
                              cancelable=cancelable)


class TestWhatTheCoverSaysBeforeTheScreenIsUp:
    """The launch's own work runs under the cover, so the cover's first line is
    written before the screen that reads it exists."""

    def test_the_first_phase_is_on_disk_before_the_screen_is_launched(self, tmp_path: Path):
        written: list[str] = []
        popen = MagicMock(side_effect=lambda *_a, **_kw: written.append(
            (tmp_path / PROGRESS_FILENAME).read_text(encoding="utf-8")) or MagicMock())

        _opened_cover(tmp_path, popen=popen)

        assert parse_progress(written[0]).message == STARTUP_PHASES[0].message

    def test_a_done_a_crash_left_there_cannot_close_the_new_cover(self, tmp_path: Path):
        """The screen closes on DONE, so a leftover one read on its first poll
        would take the cover off a launch that had only just raised it."""
        (tmp_path / PROGRESS_FILENAME).write_text("DONE", encoding="utf-8")

        cover = _opened_cover(tmp_path)

        assert not parse_progress(cover.progress_file.read_text(encoding="utf-8")).done


class TestTakingTheCoverDown:
    def test_the_screen_is_told_and_then_waited_for(self, tmp_path: Path):
        cover = _opened_cover(tmp_path)

        cover.take_it_down()

        cover.process.wait.assert_called_once()
        assert not cover.progress_file.exists()

    def test_a_screen_that_will_not_go_is_killed(self, tmp_path: Path):
        """Its window is topmost over every monitor: left standing it hides
        whatever the launch has to say, for a minute of staleness guard."""
        cover = _opened_cover(tmp_path)
        cover.process.wait.side_effect = subprocess.TimeoutExpired("loading_screen", 3.0)

        cover.take_it_down()

        cover.process.kill.assert_called_once()

    def test_a_session_with_no_cover_takes_nothing_down(self, tmp_path: Path):
        cover = open_the_cover(tmp_path, show_overlays=False, project_dirs="")

        cover.take_it_down()  # an integration run has no screen to tell

        assert not cover.progress_file.exists()


class TestWhatEscCancelsAtTheLoadingScreen:
    @classmethod
    def _opened_line(cls, state_dir, *, cancelable=True):
        cover = _opened_cover(state_dir, cancelable=cancelable)
        cover.progress.advance("services")
        return parse_progress(cover.progress_file.read_text(encoding="utf-8"))

    def test_a_launch_says_esc_cancels_opening_fun_time(self, tmp_path):
        assert self._opened_line(tmp_path).hint == "Press Esc to cancel opening Fun Time"

    def test_a_launch_arriving_from_vr_says_esc_cancels_exiting_vr(self, tmp_path):
        """He asked for the desktop from inside the headset: until it is up,
        Esc takes him back into VR."""
        raise_crossing_cover(tmp_path, DESKTOP)

        assert self._opened_line(tmp_path).hint == "Press Esc to cancel exiting VR"

    def test_a_launch_on_the_way_back_offers_no_esc(self, tmp_path):
        """Esc already called the crossing off; a second would send him back
        the other way for as long as he kept pressing it."""
        raise_crossing_cover(tmp_path, DESKTOP)

        assert self._opened_line(tmp_path, cancelable=False).hint == ""

    def test_an_esc_pressed_while_the_room_changed_over_calls_the_arrival_off(
        self, tmp_path,
    ):
        """Nothing but the hotkey script left over from the session he left was
        listening then, and the flag it dropped is his answer to this launch."""
        raise_crossing_cover(tmp_path, DESKTOP)
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        cover = _opened_cover(tmp_path)

        assert cover.progress.cancelled

    def test_the_first_line_is_not_a_checkpoint_the_flag_can_stop(self, tmp_path):
        """That flag is answered where the teardown lives, at the first phase
        with children to kill -- not here, where raising the cover would raise
        past the launch instead."""
        raise_crossing_cover(tmp_path, DESKTOP)
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        cover = _opened_cover(tmp_path)  # must not raise

        assert parse_progress(
            cover.progress_file.read_text(encoding="utf-8")).message == STARTUP_PHASES[0].message

    def test_a_launch_on_the_way_back_clears_the_esc_that_sent_it(self, tmp_path):
        raise_crossing_cover(tmp_path, DESKTOP)
        flag = tmp_path / CANCEL_FILENAME
        flag.write_text("cancel\n", encoding="utf-8")

        cover = _opened_cover(tmp_path, cancelable=False)

        assert not flag.exists()
        assert not cover.progress.cancelled


class TestEscOnTheWayBackFromACancelledCrossing:
    """A launch that IS a crossing coming back cannot be cancelled.  Esc is what
    called the crossing off; there is nowhere further back to go, and cancelling
    here closed the app out from under him -- which is not what any number of
    Escs may do."""

    def test_the_standing_cover_is_what_says_this_is_a_return(self, tmp_path):
        assert not returning_from_a_crossing(tmp_path)

        raise_crossing_cover(tmp_path, VR)
        assert returning_from_a_crossing(tmp_path)

        drop_crossing_cover(tmp_path)
        assert returning_from_a_crossing(tmp_path), (
            "DONE is the other session's word that it is up, not a deletion"
        )
