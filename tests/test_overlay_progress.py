from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.overlay_progress import (
    CANCEL_FILENAME,
    PROGRESS_FILENAME,
    SHUTDOWN_PHASES,
    SHUTDOWN_READY_FILENAME,
    STARTUP_PHASES,
    NullProgress,
    Phase,
    PhaseProgress,
    StartupCancelled,
    cancel_file_for,
    parse_progress,
    ready_file_for,
    startup_still_building,
)


TWO_PHASES = (
    Phase("quick", "Quick...", 1.0),
    Phase("slow", "Slow...", 9.0),
    Phase("done", "Done...", 0.0),
)


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

        So each phase reports the wait BEHIND it, never its own: crediting a
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

    def test_finish_writes_done(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = PhaseProgress(progress_file, phases=TWO_PHASES)
        progress.finish()

        assert progress_file.read_text(encoding="utf-8") == "DONE"


class TestStartupPhases:
    def test_every_phase_key_is_distinct(self):
        keys = [phase.key for phase in STARTUP_PHASES]
        assert len(keys) == len(set(keys))

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

        Every shutdown phase has real work behind it, so a full bar during one of
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

        progress = PhaseProgress(progress_file, phases=TWO_PHASES, cancel_file=cancel_file)

        with pytest.raises(StartupCancelled):
            progress.advance("quick")

    def test_advance_does_not_write_progress_once_cancelled(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)
        cancel_file.write_text("", encoding="utf-8")

        progress = PhaseProgress(progress_file, phases=TWO_PHASES, cancel_file=cancel_file)
        with pytest.raises(StartupCancelled):
            progress.advance("quick")

        # The cancelled step is aborted before it touches the progress file.
        assert not progress_file.exists()

    def test_advance_proceeds_while_the_flag_is_absent(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)

        progress = PhaseProgress(progress_file, phases=TWO_PHASES, cancel_file=cancel_file)
        progress.advance("quick")

        assert progress_file.read_text(encoding="utf-8") == "0/1000|Quick..."

    def test_cancelled_reflects_the_flag(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)
        progress = PhaseProgress(progress_file, phases=TWO_PHASES, cancel_file=cancel_file)

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
        """The whole ordering, walked: every companion is on screen behind the
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
