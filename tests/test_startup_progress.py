from __future__ import annotations

from pathlib import Path

from fun_time.startup_progress import StartupProgress, NullProgress


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


class TestNullProgress:
    def test_advance_is_noop(self):
        progress = NullProgress()
        progress.advance("anything")  # should not raise

    def test_finish_is_noop(self):
        progress = NullProgress()
        progress.finish()  # should not raise
