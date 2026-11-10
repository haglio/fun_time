from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest
from shared_ui.palette import LOADING_ACCENT, LOADING_STATUS

from fun_time.overlay_progress import (
    ACTIVATING_ORIGENERATOR,
    CANCEL_FILENAME,
    CANCELING,
    PROGRESS_FILENAME,
    SHUTDOWN_PROGRESS_FILENAME,
    SHUTDOWN_READY_FILENAME,
    STARTING_VOICE_COMMANDS,
    STARTUP_PHASES,
    PhaseProgress,
    StartupCancelled,
)
from fun_time.session_handoff import (
    DESKTOP,
    drop_transition_screen,
    hold_the_headset,
    raise_transition_screen,
    release_the_headset,
    say_the_crossing_is_cancelled,
)
from fun_time_vr.loading_screen import (
    CLOSING_STATUS,
    HELD_STATUS,
    LOADING_SCREEN_CLEAR,
    LOADING_SCREEN_DWELL_S,
    LOADING_SCREEN_SIZE_PX,
    SCENE_READY_FILENAME,
    SCENE_READY_GRACE_S,
    SHUTDOWN_STALE_TIMEOUT_S,
    STARTUP_STALE_TIMEOUT_S,
    VR_SHUTDOWN_PHASES,
    VR_STARTUP_PHASES,
    LoadingScreen,
    LoadingScreenAnchor,
    LoadingScreenDwell,
    LoadingScreenWatcher,
    SceneReady,
    paint_loading_screen,
    scene_ready_file,
    wait_for_loading_screen_painted,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


ESC_WORDS = "Press Esc to cancel opening Fun Time VR"
CLOSING_ESC_WORDS = "Press Esc to cancel closing Fun Time VR"


def _startup_writer(state_dir: Path, *, cancellable: bool = True) -> PhaseProgress:
    return PhaseProgress(
        state_dir / PROGRESS_FILENAME,
        phases=VR_STARTUP_PHASES,
        cancel_file=state_dir / CANCEL_FILENAME if cancellable else None,
        hint=ESC_WORDS if cancellable else "",
    )


def _shutdown_writer(state_dir: Path) -> PhaseProgress:
    return PhaseProgress(state_dir / SHUTDOWN_PROGRESS_FILENAME, phases=VR_SHUTDOWN_PHASES)


def test_it_is_drawn_into_the_eyes_which_no_desktop_window_reaches():
    headset = Path(__file__).resolve().parents[1] / "fun_time_vr"

    for module in ("loading_screen.py", "frame_loop.py"):
        source = (headset / module).read_text(encoding="utf-8")
        assert "overlay_window" not in source, module
        assert "tkinter" not in source, module


class TestThePhases:
    def _advanced_keys(self, pattern: str) -> set[str]:
        orchestrator = Path(__file__).resolve().parents[1] / "fun_time_vr" / "orchestrator.py"
        source = orchestrator.read_text(encoding="utf-8")
        return set(re.findall(pattern, source))

    def test_every_startup_phase_the_orchestrator_reports_exists(self):
        reported = self._advanced_keys(r'\bprogress\.advance\("([a-z_]+)"\)')

        assert reported == {phase.key for phase in VR_STARTUP_PHASES} - {
            ACTIVATING_ORIGENERATOR.key, STARTING_VOICE_COMMANDS.key}

    def test_it_carries_the_waits_the_desktops_own_steps_announce(self):
        keys = [phase.key for phase in VR_STARTUP_PHASES]

        assert keys[-3:] == [ACTIVATING_ORIGENERATOR.key, STARTING_VOICE_COMMANDS.key,
                             "finalizing"]

    def test_a_launch_that_opens_no_browser_or_windows_has_no_phase_for_them(self):
        the_desktops_alone = {phase.key for phase in STARTUP_PHASES} - {
            phase.key for phase in VR_STARTUP_PHASES}

        assert {"browser", "windows"} <= the_desktops_alone

    def test_every_shutdown_phase_the_orchestrator_reports_exists(self):
        reported = self._advanced_keys(r'\bshutdown\.advance\("([a-z_]+)"\)')

        assert reported | {"controls"} == {phase.key for phase in VR_SHUTDOWN_PHASES}

    def test_only_the_last_phase_claims_no_time(self):
        seconds = [phase.seconds for phase in VR_STARTUP_PHASES]

        assert seconds[-1] == 0.0
        assert all(each > 0 for each in seconds[:-1])

    def test_the_funestra_is_the_last_thing_teardown_reports(self):
        assert VR_SHUTDOWN_PHASES[-1].key == "funestras"

    def test_the_closing_screen_opens_on_its_first_phase_words(self):
        assert VR_SHUTDOWN_PHASES[0].message == CLOSING_STATUS


class TestWhatTheLoadingScreenShows:
    def test_no_files_means_show_the_scene(self, tmp_path: Path):
        assert LoadingScreenWatcher(tmp_path).read() is None

    def test_a_startup_phase_shows_its_words_a_bar_and_the_way_out(self, tmp_path: Path):
        _startup_writer(tmp_path).advance("funestras")

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.status == "Waiting for Funestras..."
        assert loading_screen.hint == ESC_WORDS
        assert 0.0 < loading_screen.fraction < 1.0
        assert not loading_screen.closing

    def test_the_weightless_last_phase_fills_the_bar(self, tmp_path: Path):
        _startup_writer(tmp_path).advance("finalizing")

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.fraction == pytest.approx(1.0)

    def test_done_takes_the_loading_screen_down(self, tmp_path: Path):
        progress = _startup_writer(tmp_path)
        progress.advance("funestras")
        watcher = LoadingScreenWatcher(tmp_path)
        assert watcher.read() is not None

        progress.finish()

        assert watcher.read() is None

    def test_a_torn_write_holds_the_last_readable_line(self, tmp_path: Path):
        _startup_writer(tmp_path).advance("funestras")
        watcher = LoadingScreenWatcher(tmp_path)
        settled = watcher.read()

        (tmp_path / PROGRESS_FILENAME).write_text("47/", encoding="utf-8")

        assert watcher.read() == settled

    def test_the_cancel_flag_replaces_the_status_and_takes_the_hint_away(
            self, tmp_path: Path):
        _startup_writer(tmp_path).advance("funestras")
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.status == CANCELING
        assert loading_screen.hint == ""

    def test_the_quit_chord_never_turns_it_to_canceling(self, tmp_path: Path):
        _startup_writer(tmp_path).advance("funestras")
        (tmp_path / CANCEL_FILENAME).write_text("quit\n", encoding="utf-8")

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.status == "Waiting for Funestras..."
        assert loading_screen.hint == ESC_WORDS

    def test_a_loading_screen_offering_no_esc_goes_on_showing_its_phases(self, tmp_path: Path):
        _startup_writer(tmp_path, cancellable=False).advance("funestras")
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.status == "Waiting for Funestras..."
        assert loading_screen.hint == ""

    def test_a_phase_still_in_flight_cannot_flip_the_words_back(self, tmp_path: Path):
        progress = _startup_writer(tmp_path)
        progress.advance("companions")
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")
        watcher = LoadingScreenWatcher(tmp_path)
        assert watcher.read().status == CANCELING

        (tmp_path / PROGRESS_FILENAME).write_text("70/1030|Waiting for Funestras...",
                                                  encoding="utf-8")

        assert watcher.read().status == CANCELING

    def test_the_cancelling_words_survive_the_flag_being_cleared(self, tmp_path: Path):
        _startup_writer(tmp_path).advance("funestras")
        cancel_file = tmp_path / CANCEL_FILENAME
        cancel_file.write_text("cancel\n", encoding="utf-8")
        watcher = LoadingScreenWatcher(tmp_path)
        assert watcher.read().status == CANCELING

        cancel_file.unlink()

        assert watcher.read().status == CANCELING

    def test_a_teardown_outranks_a_startup_file_left_lying_around(self, tmp_path: Path):
        _startup_writer(tmp_path).advance("funestras")
        _shutdown_writer(tmp_path).advance("companions")

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.closing
        assert loading_screen.status == "Closing companions..."

    def test_the_closing_screen_says_what_esc_would_cancel(self, tmp_path: Path):
        PhaseProgress(tmp_path / SHUTDOWN_PROGRESS_FILENAME, phases=VR_SHUTDOWN_PHASES,
                      hint=CLOSING_ESC_WORDS).advance("companions")

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.status == "Closing companions..."
        assert loading_screen.hint == CLOSING_ESC_WORDS

    def test_esc_turns_the_closing_screen_to_canceling(self, tmp_path: Path):
        PhaseProgress(tmp_path / SHUTDOWN_PROGRESS_FILENAME, phases=VR_SHUTDOWN_PHASES,
                      hint=CLOSING_ESC_WORDS).advance("companions")
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen == LoadingScreen(status=CANCELING, fraction=loading_screen.fraction, closing=True)

    def test_the_funestra_can_raise_the_closing_screen_with_no_file_at_all(
            self, tmp_path: Path):
        watcher = LoadingScreenWatcher(tmp_path)
        assert watcher.read() is None

        watcher.closing_unasked()

        loading_screen = watcher.read()
        assert loading_screen is not None
        assert loading_screen.closing
        assert loading_screen.status == CLOSING_STATUS


class TestTheHeldLoadingScreen:
    def test_a_hold_puts_the_crossing_panel_up_with_no_progress_file_at_all(
        self, tmp_path: Path,
    ):
        hold_the_headset(tmp_path, stop_runtime=False)

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.status == HELD_STATUS
        assert loading_screen.closing is True

    def test_it_outranks_whatever_the_teardown_file_still_says(self, tmp_path: Path):
        watcher = LoadingScreenWatcher(tmp_path)
        PhaseProgress(tmp_path / SHUTDOWN_PROGRESS_FILENAME,
                      phases=VR_SHUTDOWN_PHASES).advance("funestras")
        assert watcher.read().status != HELD_STATUS

        hold_the_headset(tmp_path, stop_runtime=False)

        assert watcher.read().status == HELD_STATUS

    def test_it_says_what_esc_would_cancel_on_the_crossing_it_covers(self, tmp_path: Path):
        raise_transition_screen(tmp_path, DESKTOP)
        hold_the_headset(tmp_path, stop_runtime=False)

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.status == HELD_STATUS
        assert loading_screen.hint == "Press Esc to cancel exiting VR"

    def test_under_fun_time_s_loading_screen_it_says_what_that_screen_offers(
        self, tmp_path: Path,
    ):
        raise_transition_screen(tmp_path, DESKTOP)
        hold_the_headset(tmp_path, stop_runtime=False)
        PhaseProgress(tmp_path / PROGRESS_FILENAME, phases=STARTUP_PHASES,
                      hint="Press Esc to cancel exiting VR").advance("services")
        drop_transition_screen(tmp_path)

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.status == HELD_STATUS
        assert loading_screen.hint == "Press Esc to cancel exiting VR"

    def test_esc_turns_it_to_canceling(self, tmp_path: Path):
        raise_transition_screen(tmp_path, DESKTOP)
        hold_the_headset(tmp_path, stop_runtime=False)
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen == LoadingScreen(status=CANCELING, fraction=1.0, closing=True)

    def test_a_crossing_turned_back_says_so_once_the_flag_is_spent(self, tmp_path: Path):
        raise_transition_screen(tmp_path, DESKTOP)
        hold_the_headset(tmp_path, stop_runtime=False)
        say_the_crossing_is_cancelled(tmp_path)

        loading_screen = LoadingScreenWatcher(tmp_path).read()

        assert loading_screen is not None
        assert loading_screen.status == CANCELING
        assert loading_screen.hint == ""

    def test_releasing_it_hands_the_view_back(self, tmp_path: Path):
        hold_the_headset(tmp_path, stop_runtime=False)
        watcher = LoadingScreenWatcher(tmp_path)
        assert watcher.read() is not None

        release_the_headset(tmp_path)

        assert watcher.read() is None


class TestGivingUp:
    def test_a_startup_file_that_stops_moving_gives_the_headset_back(
            self, tmp_path: Path):
        clock = _Clock()
        _startup_writer(tmp_path).advance("funestras")
        watcher = LoadingScreenWatcher(tmp_path, clock=clock)
        assert watcher.read() is not None

        clock.now += STARTUP_STALE_TIMEOUT_S + 1

        assert watcher.read() is None

    def test_the_clock_restarts_on_every_write(self, tmp_path: Path):
        clock = _Clock()
        progress = _startup_writer(tmp_path)
        progress.advance("services")
        watcher = LoadingScreenWatcher(tmp_path, clock=clock)
        watcher.read()

        clock.now += STARTUP_STALE_TIMEOUT_S - 1
        progress.advance("funestras")
        watcher.read()
        clock.now += STARTUP_STALE_TIMEOUT_S - 1

        assert watcher.read() is not None

    def test_two_steps_inside_one_filesystem_tick_still_restart_it(
            self, tmp_path: Path):
        clock = _Clock()
        progress = _startup_writer(tmp_path)
        progress.advance("services")
        watcher = LoadingScreenWatcher(tmp_path, clock=clock)
        watcher.read()
        stamped = (tmp_path / PROGRESS_FILENAME).stat()

        clock.now += STARTUP_STALE_TIMEOUT_S - 1
        progress.advance("funestras")
        os.utime(tmp_path / PROGRESS_FILENAME, (stamped.st_atime, stamped.st_mtime))
        watcher.read()
        clock.now += STARTUP_STALE_TIMEOUT_S - 1

        assert watcher.read() is not None

    def test_teardowns_clock_is_the_short_one(self, tmp_path: Path):
        clock = _Clock()
        _shutdown_writer(tmp_path).advance("controls")
        watcher = LoadingScreenWatcher(tmp_path, clock=clock)
        assert watcher.read() is not None

        clock.now += SHUTDOWN_STALE_TIMEOUT_S + 1

        assert watcher.read() is None

    def test_a_teardown_still_saying_it_is_going_keeps_the_headset_covered(
            self, tmp_path: Path):
        clock = _Clock()
        _shutdown_writer(tmp_path).advance("funestras")
        shutdown_file = tmp_path / SHUTDOWN_PROGRESS_FILENAME
        written_at = shutdown_file.stat().st_mtime
        watcher = LoadingScreenWatcher(tmp_path, clock=clock)
        watcher.read()

        for beat in range(1, round(SHUTDOWN_STALE_TIMEOUT_S * 2)):
            clock.now += 1
            os.utime(shutdown_file, (written_at + beat, written_at + beat))
            watcher.read()

        assert watcher.read() is not None

    def test_an_end_given_up_on_stays_given_up(self, tmp_path: Path):
        clock = _Clock()
        progress = _startup_writer(tmp_path)
        progress.advance("funestras")
        watcher = LoadingScreenWatcher(tmp_path, clock=clock)
        watcher.read()
        clock.now += STARTUP_STALE_TIMEOUT_S + 1
        assert watcher.read() is None

        progress.advance("finalizing")

        assert watcher.read() is None


class TestTheReadyFlag:
    def test_it_is_the_one_the_desktops_closing_screen_drops(self, tmp_path: Path):
        assert LoadingScreenWatcher(tmp_path).ready_file == tmp_path / SHUTDOWN_READY_FILENAME

    def test_the_flag_landing_is_what_teardown_was_waiting_for(self, tmp_path: Path):
        ready = tmp_path / SHUTDOWN_READY_FILENAME
        ready.write_text("", encoding="utf-8")

        assert wait_for_loading_screen_painted(
            ready, still_alive=lambda: True, timeout_s=5.0, sleep=lambda _s: None)

    def test_a_funestra_that_is_already_gone_is_not_waited_for(self, tmp_path: Path):
        assert not wait_for_loading_screen_painted(
            tmp_path / SHUTDOWN_READY_FILENAME,
            still_alive=lambda: False, timeout_s=5.0, sleep=lambda _s: None,
        )

    def test_a_silent_funestra_is_given_up_on_at_the_deadline(self, tmp_path: Path):
        clock = _Clock()

        def sleep(seconds: float) -> None:
            clock.now += seconds

        assert not wait_for_loading_screen_painted(
            tmp_path / SHUTDOWN_READY_FILENAME,
            still_alive=lambda: True, timeout_s=1.0, clock=clock, sleep=sleep,
        )


class TestPainting:
    @pytest.mark.parametrize("loading_screen", [
        LoadingScreen(status="Waiting for Funestras...", fraction=0.4, hint=ESC_WORDS),
        LoadingScreen(status=CANCELING, fraction=0.4),
        LoadingScreen(status="Closing Funestras...", fraction=1.0, closing=True),
        LoadingScreen(status="", fraction=0.0),
    ])
    def test_every_loading_screen_paints_at_the_one_size(self, loading_screen: LoadingScreen):
        image = paint_loading_screen(loading_screen)

        assert image.size == LOADING_SCREEN_SIZE_PX
        assert image.mode == "RGBA"

    def test_the_bar_is_clamped_to_the_panel(self):
        for fraction in (-1.0, 2.0):
            assert paint_loading_screen(LoadingScreen(status="x", fraction=fraction)).size == LOADING_SCREEN_SIZE_PX

    def test_it_is_the_familys_loading_panel_on_that_ground(self):
        image = paint_loading_screen(LoadingScreen(status="Waiting for Funestras...", fraction=0.5))

        pixels = image.load()
        tones = {pixels[x, y][:3] for y in range(image.height) for x in range(image.width)}
        assert LOADING_STATUS in tones
        assert LOADING_ACCENT in tones

    def test_the_ground_is_the_panels_own_tone(self):
        image = paint_loading_screen(LoadingScreen(status="x", fraction=0.0))

        assert tuple(round(c * 255) for c in LOADING_SCREEN_CLEAR[:3]) == image.getpixel((0, 0))[:3]


def test_a_checkpoint_still_raises_for_the_vr_phases(tmp_path: Path):
    progress = _startup_writer(tmp_path)
    (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

    with pytest.raises(StartupCancelled):
        progress.advance("funestras")


class TestTheRoomComingUp:
    def test_nothing_is_said_while_the_room_is_still_blank(self, tmp_path: Path):
        marker = tmp_path / SCENE_READY_FILENAME
        ready = SceneReady(marker, clock=_Clock())

        for _frame in range(10):
            ready.note(blank=("portrait",), read=True)

        assert not marker.exists()

    def test_nothing_is_said_while_the_loading_screen_has_not_been_on_screen_long_enough(
            self, tmp_path: Path):
        marker = tmp_path / SCENE_READY_FILENAME
        ready = SceneReady(marker, clock=_Clock())

        for _frame in range(10):
            ready.note(blank=(), read=False)

        assert not marker.exists()

    def test_the_first_full_frame_says_so(self, tmp_path: Path):
        marker = tmp_path / SCENE_READY_FILENAME
        ready = SceneReady(marker, clock=_Clock())
        ready.note(blank=("portrait",), read=True)

        ready.note(blank=(), read=True)

        assert marker.exists()

    def test_it_is_said_once_and_not_again(self, tmp_path: Path):
        marker = tmp_path / SCENE_READY_FILENAME
        ready = SceneReady(marker, clock=_Clock())
        ready.note(blank=(), read=True)
        marker.unlink()

        ready.note(blank=(), read=True)

        assert not marker.exists()

    def test_a_screen_that_never_fills_does_not_hold_the_reveal_forever(
            self, tmp_path: Path):
        clock = _Clock()
        marker = tmp_path / SCENE_READY_FILENAME
        ready = SceneReady(marker, grace_s=SCENE_READY_GRACE_S, clock=clock)
        ready.note(blank=("portrait",), read=True)

        clock.now += SCENE_READY_GRACE_S + 1
        ready.note(blank=("portrait",), read=True)

        assert marker.exists()

    def test_the_screens_with_no_frame_are_named_when_the_grace_runs_out(
            self, tmp_path: Path, caplog):
        clock = _Clock()
        ready = SceneReady(tmp_path / SCENE_READY_FILENAME,
                           grace_s=SCENE_READY_GRACE_S, clock=clock)
        ready.note(blank=("portrait", "panel"), read=True)

        clock.now += SCENE_READY_GRACE_S + 1
        with caplog.at_level(logging.WARNING):
            ready.note(blank=("portrait", "panel"), read=True)

        assert "portrait" in caplog.text
        assert "panel" in caplog.text

    def test_a_full_room_revealed_on_the_grace_blames_the_loading_screen_not_a_screen(
            self, tmp_path: Path, caplog):
        clock = _Clock()
        ready = SceneReady(tmp_path / SCENE_READY_FILENAME,
                           grace_s=SCENE_READY_GRACE_S, clock=clock)
        ready.note(blank=(), read=False)

        clock.now += SCENE_READY_GRACE_S + 1
        with caplog.at_level(logging.WARNING):
            ready.note(blank=(), read=False)

        assert "loading screen" in caplog.text
        assert "blank" not in caplog.text

    def test_both_ends_derive_the_marker_from_the_state_dir(self, tmp_path: Path):
        assert scene_ready_file(tmp_path) == tmp_path / SCENE_READY_FILENAME


class TestWhereTheLoadingScreenHangs:
    def test_the_first_heading_is_the_one_it_keeps(self):
        anchor = LoadingScreenAnchor()

        assert anchor.heading(1.2) == pytest.approx(1.2)
        assert anchor.heading(2.9) == pytest.approx(1.2)
        assert anchor.heading(-0.4) == pytest.approx(1.2)

    def test_a_released_anchor_takes_the_next_viewer_where_they_are(self):
        anchor = LoadingScreenAnchor()
        anchor.heading(1.2)

        anchor.release()

        assert anchor.heading(2.9) == pytest.approx(2.9)

    def test_a_heading_of_zero_is_a_heading(self):
        anchor = LoadingScreenAnchor()
        anchor.heading(0.0)

        assert anchor.heading(2.9) == pytest.approx(0.0)

    def test_it_follows_him_until_the_panel_has_been_read(self):
        anchor = LoadingScreenAnchor()

        assert anchor.heading(1.2, settled=False) == pytest.approx(1.2)
        assert anchor.heading(2.9, settled=False) == pytest.approx(2.9)
        assert anchor.held is None

        assert anchor.heading(-0.4, settled=True) == pytest.approx(-0.4)
        assert anchor.heading(2.9, settled=True) == pytest.approx(-0.4)

    def test_what_it_holds_is_readable_so_the_log_can_say_where_it_went(self):
        anchor = LoadingScreenAnchor()
        assert anchor.held is None

        anchor.heading(1.2)

        assert anchor.held == pytest.approx(1.2)


class TestBeingSeen:
    def test_frames_that_reached_nobody_do_not_count(self):
        clock = _Clock()
        seen = LoadingScreenDwell(clock=clock)

        for _frame in range(500):
            seen.note(False)
            clock.now += 0.1

        assert not seen.dwelt

    def test_the_dwell_runs_from_the_first_frame_that_landed(self):
        clock = _Clock()
        seen = LoadingScreenDwell(clock=clock)
        clock.now += 60

        seen.note(True)
        assert not seen.dwelt

        clock.now += LOADING_SCREEN_DWELL_S
        assert seen.dwelt

    def test_a_headset_taken_off_after_the_dwell_does_not_restart_it(self):
        clock = _Clock()
        seen = LoadingScreenDwell(clock=clock)
        seen.note(True)

        clock.now += LOADING_SCREEN_DWELL_S
        seen.note(False)

        assert seen.dwelt

    def test_a_gap_before_the_dwell_is_done_starts_it_over(self):
        clock = _Clock()
        seen = LoadingScreenDwell(clock=clock)

        seen.note(True)
        clock.now += LOADING_SCREEN_DWELL_S - 0.1
        seen.note(False)
        clock.now += 1.0

        assert not seen.dwelt

        seen.note(True)
        clock.now += LOADING_SCREEN_DWELL_S
        seen.note(True)
        assert seen.dwelt
