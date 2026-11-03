from __future__ import annotations

import sys
from pathlib import Path

from fun_time import closing_screen
from fun_time.closing_screen import WINDOW_TITLE
from fun_time.overlay_progress import ready_file_for


class FakeOverlay:
    def __init__(self, progress_file, **kwargs):
        self.progress_file = progress_file
        self.kwargs = kwargs
        self.on_shown = None

    def run(self, on_shown=None):
        self.on_shown = on_shown


class TestWindowTitle:
    def test_title_cannot_be_mistaken_for_the_dashboard(self):
        assert WINDOW_TITLE != "Fun Time"
        assert "Fun Time" in WINDOW_TITLE


class TestMain:
    def _run_main(self, tmp_path, monkeypatch) -> tuple[Path, FakeOverlay]:
        progress_file = tmp_path / "shutdown_progress.txt"
        built: list[FakeOverlay] = []

        def build(*args, **kwargs):
            built.append(FakeOverlay(*args, **kwargs))
            return built[-1]

        monkeypatch.setattr(closing_screen.OverlayWindow, "over_every_monitor", build)
        monkeypatch.setattr(sys, "argv", ["closing_screen", str(progress_file)])

        closing_screen.main()
        return progress_file, built[0]

    def test_covers_the_progress_file_it_was_given(self, tmp_path, monkeypatch):
        progress_file, overlay = self._run_main(tmp_path, monkeypatch)

        assert overlay.progress_file == progress_file
        assert overlay.kwargs["title"] == WINDOW_TITLE

    def test_takes_over_from_no_launchers_loading_screen_and_leaves_it_nothing(self, tmp_path, monkeypatch):
        _progress_file, overlay = self._run_main(tmp_path, monkeypatch)

        assert overlay.kwargs.get("taking_over") is None

    def test_reports_ready_only_once_the_loading_screen_is_painted(self, tmp_path, monkeypatch):
        progress_file, overlay = self._run_main(tmp_path, monkeypatch)

        assert not ready_file_for(progress_file).exists()

        overlay.on_shown()

        assert ready_file_for(progress_file).exists()
