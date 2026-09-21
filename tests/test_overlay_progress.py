from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.overlay_progress import (
    CANCEL_FILENAME,
    CANCEL_OPENING_FUN_TIME,
    CANCEL_WORD,
    LAUNCH_PHASES,
    PROGRESS_FILENAME,
    QUIT_WORD,
    ROOM_PHASES,
    SHUTDOWN_PHASES,
    SHUTDOWN_READY_FILENAME,
    STARTUP_PHASES,
    NullProgress,
    Phase,
    PhaseProgress,
    Progress,
    StartupCancelled,
    cancel_file_for,
    parse_progress,
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
        """One tenth of the wait moves the bar one tenth, not a third of it.

        A step counter gave every phase the same share, so the longest one — the
        wait for the players' windows — held the bar at 83% for most of startup
        while four sub-second phases spent the rest of it.  Phases are weighted by
        how long they take, in hundredths of a second.
        """
        progress_file = tmp_path / "progress.txt"
        progress = PhaseProgress(progress_file, phases=TWO_PHASES)

        progress.advance("quick")
        assert progress_file.read_text(encoding="utf-8") == "0/1000|Quick..."

        progress.advance("slow")
        assert progress_file.read_text(encoding="utf-8") == "100/1000|Slow..."

    def test_only_the_final_phase_puts_the_bar_on_the_total(self, tmp_path: Path):
        """A full bar means "the last phase has begun", nothing sooner.

        So each phase reports the wait BEFORE it, never its own: crediting a
        phase's time as it began would read as finished one phase early, and the
        companions take that reading as their cue to show themselves.
        """
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

    def test_an_announced_phase_is_not_a_checkpoint(self, tmp_path: Path):
        """The launch names what it is doing before it has anything to tear
        down; the cancel waiting on disk is answered at the first phase that
        does, which is where the teardown lives."""
        progress_file = tmp_path / PROGRESS_FILENAME
        cancel_file_for(progress_file).write_text(f"{CANCEL_WORD}\n", encoding="utf-8")
        progress = PhaseProgress(progress_file, phases=TWO_PHASES,
                                 cancel_file=cancel_file_for(progress_file),
                                 hint=CANCEL_OPENING_FUN_TIME)

        progress.announce("quick")

        assert parse_progress(progress_file.read_text(encoding="utf-8")).message == "Quick..."
        with pytest.raises(StartupCancelled):
            progress.advance("slow")

    def test_a_null_reporter_answers_an_announcement_too(self, tmp_path: Path):
        NullProgress().announce("quick")  # must not raise: integration runs with no cover

    def test_finish_writes_done(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = PhaseProgress(progress_file, phases=TWO_PHASES)
        progress.finish()

        assert progress_file.read_text(encoding="utf-8") == "DONE"


class TestStartupPhases:
    def test_every_phase_key_is_distinct(self):
        keys = [phase.key for phase in STARTUP_PHASES]
        assert len(keys) == len(set(keys))

    def test_the_launch_names_its_own_work_before_the_room_arrives(self):
        """Loading itself and checking the players' engine took seconds off an
        uncovered desktop; they run under the cover, so the cover says so."""
        assert (*LAUNCH_PHASES, *ROOM_PHASES) == STARTUP_PHASES
        assert [phase.key for phase in LAUNCH_PHASES] == ["starting", "engine"]

    def test_the_last_phase_is_the_one_the_companions_wait_for(self):
        # Entering the last phase must land the bar on the total, which happens
        # only if that phase claims no time of its own.  That full bar is what
        # tells a companion window to show itself while the cover is still up.
        assert STARTUP_PHASES[-1].weight == 0.0
        assert all(phase.weight > 0 for phase in STARTUP_PHASES[:-1])


class TestShutdownPhases:
    def test_every_phase_key_is_distinct(self):
        keys = [phase.key for phase in SHUTDOWN_PHASES]
        assert len(keys) == len(set(keys))

    def test_no_phase_lands_the_bar_on_the_total(self, tmp_path: Path):
        """Teardown's bar never reads full before teardown is finished.

        Every shutdown phase has real work to do, so a full bar during one of
        them would say the room was clear with children still being killed — and
        windows going out one by one is the whole thing the cover is there for.
        """
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

        # The cancelled step is aborted before it touches the progress file.
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
        progress.advance("anything")  # must not raise
        assert progress.cancelled is False


class TestStartupStillBuilding:
    """Which reading of the progress file tells a companion window to show itself.

    Not "the cover has gone": the cover is the last thing to leave, so a
    companion that waits for it arrives after the reveal — the user watching the
    session's own control panel turn up on a room that was supposed to be
    finished.  It waits for the FULL BAR instead, which the final phase writes
    while the cover is still up.
    """

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

    def test_false_once_the_cover_has_been_told_to_go(self, tmp_path: Path):
        assert startup_still_building(self._state_dir(tmp_path, "DONE")) is False

    def test_the_companions_are_told_before_the_cover_is(self, tmp_path: Path):
        """The whole ordering, walked: every companion is on screen under the
        cover before anything asks the cover to leave.

        The cover closes on the DONE flag alone (``OverlayWindow._poll``), and
        that is written only by ``finish()`` — one full sequence after the
        reading the companions act on.
        """
        progress_file = tmp_path / PROGRESS_FILENAME
        progress = PhaseProgress(progress_file, phases=STARTUP_PHASES)
        cover_may_close = lambda: parse_progress(  # noqa: E731
            progress_file.read_text(encoding="utf-8")).done

        for phase in STARTUP_PHASES[:-1]:
            progress.advance(phase.key)
            assert startup_still_building(tmp_path) is True
            assert cover_may_close() is False

        progress.advance(STARTUP_PHASES[-1].key)
        # The companions show themselves here — and the cover is still up.
        assert startup_still_building(tmp_path) is False
        assert cover_may_close() is False

        progress.finish()
        assert cover_may_close() is True
