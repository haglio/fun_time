from __future__ import annotations

from pathlib import Path

from fun_time.loading_screen import WINDOW_TITLE, request_startup_cancel
from fun_time.overlay_progress import cancel_file_for


class TestRequestStartupCancel:
    def test_writes_the_cancel_flag_beside_the_progress_file(self, tmp_path: Path):
        progress_file = tmp_path / "startup_progress.txt"

        request_startup_cancel(progress_file)

        assert cancel_file_for(progress_file).exists()

    def test_is_idempotent(self, tmp_path: Path):
        progress_file = tmp_path / "startup_progress.txt"

        request_startup_cancel(progress_file)
        request_startup_cancel(progress_file)  # must not raise

        assert cancel_file_for(progress_file).exists()


class TestWindowTitle:
    def test_title_cannot_be_mistaken_for_the_dashboard(self):
        """Dashboard lookups match the exact title "Fun Time"; the borderless
        loading overlay must present a different exact title so it can never
        be resolved (and z-order-managed) as the dashboard."""
        assert WINDOW_TITLE != "Fun Time"
        assert "Fun Time" in WINDOW_TITLE  # still recognizably ours
