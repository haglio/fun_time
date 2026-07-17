from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.startup_progress import (
    CANCEL_FILENAME,
    NullProgress,
    StartupCancelled,
    StartupProgress,
    cancel_file_for,
)


class TestStartupProgress:
    def test_advance_writes_step_and_message(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = StartupProgress(progress_file, total_steps=5)
        progress.advance("Loading stuff...")

        assert progress_file.read_text(encoding="utf-8") == "1/5|Loading stuff..."

    def test_advance_increments_step(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = StartupProgress(progress_file, total_steps=3)
        progress.advance("Step one")
        progress.advance("Step two")

        assert progress_file.read_text(encoding="utf-8") == "2/3|Step two"

    def test_finish_writes_done(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = StartupProgress(progress_file, total_steps=2)
        progress.finish()

        assert progress_file.read_text(encoding="utf-8") == "DONE"


class TestCancelFileFor:
    def test_places_the_flag_beside_the_progress_file(self, tmp_path: Path):
        progress_file = tmp_path / "state" / "startup_progress.txt"
        assert cancel_file_for(progress_file) == tmp_path / "state" / CANCEL_FILENAME


class TestStartupProgressCancellation:
    def test_advance_raises_when_the_cancel_flag_is_present(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)
        cancel_file.write_text("", encoding="utf-8")

        progress = StartupProgress(progress_file, total_steps=5, cancel_file=cancel_file)

        with pytest.raises(StartupCancelled):
            progress.advance("Preparing services...")

    def test_advance_does_not_write_progress_once_cancelled(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)
        cancel_file.write_text("", encoding="utf-8")

        progress = StartupProgress(progress_file, total_steps=5, cancel_file=cancel_file)
        with pytest.raises(StartupCancelled):
            progress.advance("Preparing services...")

        # The cancelled step is aborted before it touches the progress file.
        assert not progress_file.exists()

    def test_advance_proceeds_while_the_flag_is_absent(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)

        progress = StartupProgress(progress_file, total_steps=5, cancel_file=cancel_file)
        progress.advance("Preparing services...")

        assert progress_file.read_text(encoding="utf-8") == "1/5|Preparing services..."

    def test_cancelled_reflects_the_flag(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        cancel_file = cancel_file_for(progress_file)
        progress = StartupProgress(progress_file, total_steps=5, cancel_file=cancel_file)

        assert progress.cancelled is False
        cancel_file.write_text("", encoding="utf-8")
        assert progress.cancelled is True

    def test_without_a_cancel_file_advance_never_cancels(self, tmp_path: Path):
        progress_file = tmp_path / "progress.txt"
        progress = StartupProgress(progress_file, total_steps=5)

        progress.advance("Preparing services...")
        assert progress.cancelled is False


class TestNullProgressCancellation:
    def test_null_progress_never_cancels(self):
        progress = NullProgress()
        progress.advance("anything")  # must not raise
        assert progress.cancelled is False
