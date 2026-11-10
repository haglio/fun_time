from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from fun_time import overlay_progress
from fun_time.overlay_progress import (
    ACTIVATING_ORIGENERATOR,
    APP_PHASES,
    CANCEL_FILENAME,
    CANCEL_OPENING_FUN_TIME,
    CANCEL_WORD,
    LAUNCH_PHASES,
    PROGRESS_FILENAME,
    QUIT_WORD,
    SHUTDOWN_PHASES,
    SHUTDOWN_READY_FILENAME,
    STARTING_VOICE_COMMANDS,
    STARTUP_PHASES,
    NullProgress,
    Phase,
    PhaseProgress,
    Progress,
    StartupCancelled,
    cancel_file_for,
    keep_the_loading_screen_up,
    parse_progress,
    progress_file_argument,
    ready_file_for,
    startup_still_building,
    what_the_flag_asks,
)

TWO_PHASES = (
    Phase("quick", "Quick...", 1.0),
    Phase("slow", "Slow...", 9.0),
    Phase("done", "Done...", 0.0),
)


class TestWhatTheFlagAsks:
    def test_the_quit_chord_outranks_an_esc_pressed_before_it(self, tmp_path: Path):
        flag = tmp_path / CANCEL_FILENAME
        flag.write_text("cancel\nquit\n", encoding="utf-8")

        assert what_the_flag_asks(flag) == QUIT_WORD

    def test_esc_alone_asks_for_a_cancel(self, tmp_path: Path):
        flag = tmp_path / CANCEL_FILENAME
        flag.write_text("cancel\ncancel\n", encoding="utf-8")

        assert what_the_flag_asks(flag) == CANCEL_WORD

    def test_no_flag_asks_nothing(self, tmp_path: Path):
        assert what_the_flag_asks(tmp_path / CANCEL_FILENAME) == ""


class TestWhatEscWouldCancel:
    def test_a_line_carries_the_words_under_its_bar(self):
        assert parse_progress("3/7|Preparing services...|Press Esc to cancel opening Fun Time") == (
            Progress(step=3, total=7, message="Preparing services...",
                     hint="Press Esc to cancel opening Fun Time"))

    def test_a_writer_given_those_words_puts_them_under_every_phase(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = PhaseProgress(progress_file, phases=TWO_PHASES,
                                 hint="Press Esc to cancel opening Fun Time")

        progress.advance("slow")

        assert parse_progress(progress_file.read_text(encoding="utf-8")) == Progress(
            step=100, total=1000, message="Slow...", hint="Press Esc to cancel opening Fun Time")

    def test_a_launch_offering_no_esc_is_not_called_off_by_one(self, tmp_path: Path):
        progress_file = tmp_path / PROGRESS_FILENAME
        cancel_file_for(progress_file).write_text("cancel\n", encoding="utf-8")
        progress = PhaseProgress(progress_file, phases=TWO_PHASES,
                                 cancel_file=cancel_file_for(progress_file))

        progress.advance("slow")

        assert not progress.cancelled

    def test_the_quit_chord_calls_off_even_a_launch_offering_no_esc(self, tmp_path: Path):
        progress_file = tmp_path / PROGRESS_FILENAME
        cancel_file_for(progress_file).write_text("quit\n", encoding="utf-8")
        progress = PhaseProgress(progress_file, phases=TWO_PHASES,
                                 cancel_file=cancel_file_for(progress_file))

        with pytest.raises(StartupCancelled):
            progress.advance("slow")


class TestPhaseProgress:
    def test_advance_writes_the_phase_message(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = PhaseProgress(progress_file, phases=TWO_PHASES)
        progress.advance("quick")

        assert progress_file.read_text(encoding="utf-8").endswith("|Quick...")

    def test_the_bar_tracks_time_spent_not_steps_taken(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = PhaseProgress(progress_file, phases=TWO_PHASES)

        progress.advance("quick")
        assert progress_file.read_text(encoding="utf-8") == "0/1000|Quick..."

        progress.advance("slow")
        assert progress_file.read_text(encoding="utf-8") == "100/1000|Slow..."

    def test_a_phase_beginning_is_credited_only_the_waits_before_it_never_its_own(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = PhaseProgress(progress_file, phases=TWO_PHASES)

        progress.advance("slow")
        assert progress_file.read_text(encoding="utf-8") == "100/1000|Slow..."

        progress.advance("done")
        assert progress_file.read_text(encoding="utf-8") == "1000/1000|Done..."

    def test_an_unknown_phase_is_an_error_not_a_silent_miscount(self, tmp_path: Path):
        progress = PhaseProgress(tmp_path / "progress.txt", phases=TWO_PHASES)

        with pytest.raises(KeyError):
            progress.advance("nonesuch")

    def test_an_announced_phase_reads_exactly_as_an_advanced_one(self, tmp_path: Path):
        announced, advanced = tmp_path / "announced.txt", tmp_path / "advanced.txt"
        PhaseProgress(announced, phases=TWO_PHASES).announce("slow")
        PhaseProgress(advanced, phases=TWO_PHASES).advance("slow")

        assert announced.read_text(encoding="utf-8") == advanced.read_text(encoding="utf-8")

    def test_a_cancel_waits_past_an_announcement_for_the_first_phase_with_a_teardown(self, tmp_path: Path):
        progress_file = tmp_path / PROGRESS_FILENAME
        cancel_file_for(progress_file).write_text(f"{CANCEL_WORD}\n", encoding="utf-8")
        progress = PhaseProgress(progress_file, phases=TWO_PHASES,
                                 cancel_file=cancel_file_for(progress_file),
                                 hint=CANCEL_OPENING_FUN_TIME)

        progress.announce("quick")

        assert parse_progress(progress_file.read_text(encoding="utf-8")).message == "Quick..."
        with pytest.raises(StartupCancelled):
            progress.advance("slow")

    def test_finish_writes_done(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = PhaseProgress(progress_file, phases=TWO_PHASES)
        progress.finish()

        assert progress_file.read_text(encoding="utf-8") == "DONE"


class TestStartupPhases:
    def test_every_phase_key_is_distinct(self):
        keys = [phase.key for phase in STARTUP_PHASES]
        assert len(keys) == len(set(keys))

    def test_the_waits_under_the_loading_screen_run_before_the_line_that_fills_the_bar(self):
        keys = [phase.key for phase in STARTUP_PHASES]

        assert keys[-3:] == [ACTIVATING_ORIGENERATOR.key, STARTING_VOICE_COMMANDS.key,
                             "finalizing"]

    def test_the_activating_step_names_the_app_whichever_of_its_windows_come_back(self):
        assert ACTIVATING_ORIGENERATOR.message == "Activating Origenerator..."

    def test_the_launchs_own_seconds_of_work_are_named_on_the_loading_screen(self):
        assert (*LAUNCH_PHASES, *APP_PHASES) == STARTUP_PHASES
        assert [phase.key for phase in LAUNCH_PHASES] == ["starting", "engine"]

    def test_only_the_last_phase_claims_no_time(self):
        seconds = [phase.seconds for phase in STARTUP_PHASES]

        assert seconds[-1] == 0.0
        assert all(each > 0 for each in seconds[:-1])


class TestShutdownPhases:
    def test_every_phase_key_is_distinct(self):
        keys = [phase.key for phase in SHUTDOWN_PHASES]
        assert len(keys) == len(set(keys))

    def test_each_closing_part_is_one_step_of_the_bar_whatever_it_takes(self):
        assert len({phase.seconds for phase in SHUTDOWN_PHASES}) == 1

    def test_no_phase_lands_the_bar_on_the_total(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = PhaseProgress(progress_file, phases=SHUTDOWN_PHASES)

        for phase in SHUTDOWN_PHASES:
            progress.advance(phase.key)
            position = progress_file.read_text(encoding="utf-8").split("|")[0]
            done, total = (int(part) for part in position.split("/"))
            assert done < total


class TestCancelFileFor:
    def test_places_the_flag_beside_the_progress_file(self, tmp_path: Path):
        progress_file = tmp_path / "state" / "startup_progress.txt"
        assert cancel_file_for(progress_file) == tmp_path / "state" / CANCEL_FILENAME


class TestReadyFileFor:
    def test_places_the_flag_beside_the_progress_file(self, tmp_path: Path):
        progress_file = tmp_path / "state" / "shutdown_progress.txt"
        assert ready_file_for(progress_file) == tmp_path / "state" / SHUTDOWN_READY_FILENAME


class TestProgressFileArgument:
    def test_a_loading_screen_screen_covers_the_progress_file_it_was_started_with(self, tmp_path: Path,
                                                                      monkeypatch):
        monkeypatch.setattr(sys, "argv", ["screen", str(tmp_path / "progress.txt")])

        assert progress_file_argument("fun_time.closing_screen") == tmp_path / "progress.txt"

    def test_a_loading_screen_screen_started_without_one_says_how_to_start_it_and_stops(self, monkeypatch,
                                                                              capsys):
        monkeypatch.setattr(sys, "argv", ["screen"])

        with pytest.raises(SystemExit) as stopped:
            progress_file_argument("fun_time.closing_screen")

        assert stopped.value.code == 1
        assert "python -m fun_time.closing_screen <progress_file>" in capsys.readouterr().err


class TestPhaseProgressCancellation:
    def test_advance_raises_when_the_cancel_flag_is_present(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)
        cancel_file.write_text("", encoding="utf-8")

        progress = PhaseProgress(progress_file, phases=TWO_PHASES, cancel_file=cancel_file,
                                 hint=CANCEL_OPENING_FUN_TIME)

        with pytest.raises(StartupCancelled):
            progress.advance("quick")

    def test_advance_does_not_write_progress_once_cancelled(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)
        cancel_file.write_text("", encoding="utf-8")

        progress = PhaseProgress(progress_file, phases=TWO_PHASES, cancel_file=cancel_file,
                                 hint=CANCEL_OPENING_FUN_TIME)
        with pytest.raises(StartupCancelled):
            progress.advance("quick")

        assert not progress_file.exists()

    def test_advance_proceeds_while_the_flag_is_absent(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)

        progress = PhaseProgress(progress_file, phases=TWO_PHASES, cancel_file=cancel_file,
                                 hint=CANCEL_OPENING_FUN_TIME)
        progress.advance("quick")

        assert progress_file.read_text(encoding="utf-8") == (
            f"0/1000|Quick...|{CANCEL_OPENING_FUN_TIME}")

    def test_cancelled_reflects_the_flag(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)
        progress = PhaseProgress(progress_file, phases=TWO_PHASES, cancel_file=cancel_file,
                                 hint=CANCEL_OPENING_FUN_TIME)

        assert progress.cancelled is False
        cancel_file.write_text("", encoding="utf-8")
        assert progress.cancelled is True

    def test_without_a_cancel_file_advance_never_cancels(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = PhaseProgress(progress_file, phases=TWO_PHASES)

        progress.advance("quick")
        assert progress.cancelled is False


class TestNullProgressCancellation:
    def test_null_progress_never_cancels(self):
        progress = NullProgress()
        progress.advance("anything")
        assert progress.cancelled is False


class TestKeepingTheLoadingScreenUp:
    def test_a_done_file_left_behind_is_not_made_to_look_like_work_going_on(
            self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(overlay_progress, "LOADING_SCREEN_HEARTBEAT_S", 0.01)
        progress_file = tmp_path / PROGRESS_FILENAME
        progress_file.write_text("DONE", encoding="utf-8")
        long_ago = time.time() - 100
        os.utime(progress_file, (long_ago, long_ago))

        stop = keep_the_loading_screen_up(progress_file, name="test-heartbeat")
        time.sleep(0.1)
        stop()

        assert progress_file.stat().st_mtime == pytest.approx(long_ago, abs=0.01)

    def test_a_phase_still_in_flight_is_touched_each_beat(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(overlay_progress, "LOADING_SCREEN_HEARTBEAT_S", 0.01)
        progress_file = tmp_path / PROGRESS_FILENAME
        progress_file.write_text("70/340|Launching companions...", encoding="utf-8")
        long_ago = time.time() - 100
        os.utime(progress_file, (long_ago, long_ago))

        stop = keep_the_loading_screen_up(progress_file, name="test-heartbeat")
        time.sleep(0.1)
        stop()

        assert progress_file.stat().st_mtime > long_ago + 50

    def test_a_beat_never_writes_the_line_a_screen_could_read_torn(
            self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(overlay_progress, "LOADING_SCREEN_HEARTBEAT_S", 0.01)
        progress_file = tmp_path / PROGRESS_FILENAME
        progress_file.write_text("70/340|Launching companions...", encoding="utf-8")
        written: list[Path] = []
        monkeypatch.setattr(Path, "write_text", lambda path, *_a, **_kw: written.append(path))
        monkeypatch.setattr(Path, "write_bytes", lambda path, *_a, **_kw: written.append(path))

        stop = keep_the_loading_screen_up(progress_file, name="test-heartbeat")
        time.sleep(0.1)
        stop()

        assert written == []


class TestStartupStillBuilding:
    def _state_dir(self, tmp_path: Path, text: str | None) -> Path:
        if text is not None:
            (tmp_path / PROGRESS_FILENAME).write_text(text, encoding="utf-8")
        return tmp_path

    def test_no_file_means_no_startup_to_wait_for(self, tmp_path: Path):
        assert startup_still_building(self._state_dir(tmp_path, None)) is False

    def test_true_while_the_phases_are_still_running(self, tmp_path: Path):
        assert startup_still_building(
            self._state_dir(tmp_path, "70/340|Launching companions...")) is True

    def test_false_on_the_full_bar_the_final_phase_writes(self, tmp_path: Path):
        assert startup_still_building(
            self._state_dir(tmp_path, "340/340|Finalizing...")) is False

    def test_false_once_the_loading_screen_has_been_told_to_go(self, tmp_path: Path):
        assert startup_still_building(self._state_dir(tmp_path, "DONE")) is False

    def test_the_companions_are_told_before_the_loading_screen_is(self, tmp_path: Path):
        progress_file = tmp_path / PROGRESS_FILENAME
        progress = PhaseProgress(progress_file, phases=STARTUP_PHASES)
        loading_screen_may_close = lambda: parse_progress(  # noqa: E731
            progress_file.read_text(encoding="utf-8")).done

        carries_time = [phase for phase in STARTUP_PHASES if phase.seconds > 0]
        for phase in carries_time:
            progress.advance(phase.key)
            assert startup_still_building(tmp_path) is True
            assert loading_screen_may_close() is False

        for phase in STARTUP_PHASES[len(carries_time):]:
            progress.advance(phase.key)
            assert startup_still_building(tmp_path) is False
            assert loading_screen_may_close() is False

        progress.finish()
        assert loading_screen_may_close() is True
