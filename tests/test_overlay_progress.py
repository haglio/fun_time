from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.overlay_progress import (
    CANCEL_FILENAME,
    SHUTDOWN_PHASES,
    SHUTDOWN_READY_FILENAME,
    STARTUP_PHASES,
    NullProgress,
    Phase,
    PhaseProgress,
    StartupCancelled,
    cancel_file_for,
    ready_file_for,
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
        """The loading screen closes the moment the bar reaches the total.

        So each phase reports the wait BEHIND it, never its own: crediting a
        phase's time as it began would hit the total one phase early and tear the
        overlay down over a desktop still being arranged.
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

    def test_the_last_phase_is_the_one_that_closes_the_overlay(self):
        # Entering the last phase must land the bar on the total, which happens
        # only if that phase claims no time of its own.
        assert STARTUP_PHASES[-1].weight == 0.0
        assert all(phase.weight > 0 for phase in STARTUP_PHASES[:-1])


class TestShutdownPhases:
    def test_every_phase_key_is_distinct(self):
        keys = [phase.key for phase in SHUTDOWN_PHASES]
        assert len(keys) == len(set(keys))

    def test_no_phase_lands_the_bar_on_the_total(self, tmp_path: Path):
        """Teardown ends on DONE, never on a weightless final phase.

        Every shutdown phase has real work behind it, so one of them reaching
        the total would drop the cover with children still being killed — and
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
