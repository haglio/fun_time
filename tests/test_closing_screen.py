from __future__ import annotations

import sys
from pathlib import Path

from fun_time import closing_screen
from fun_time.closing_screen import WINDOW_TITLE
from fun_time.overlay_progress import ready_file_for


class FakeOverlay:
    """Stands in for the real tkinter cover, which would put a full-screen
    window over the machine running the suite."""

    def __init__(self, progress_file, **kwargs):
        self.progress_file = progress_file
        self.kwargs = kwargs
        self.on_shown = None

    def run(self, on_shown=None):
        self.on_shown = on_shown


class TestWindowTitle:
    def test_title_cannot_be_mistaken_for_the_dashboard(self):
        """The dispatch loop resolves the dashboard by the exact title "Fun
        Time" and is still running when this cover goes up, so the cover must
        never answer to that name — it would be managed as the dashboard."""
        assert WINDOW_TITLE != "Fun Time"
        assert "Fun Time" in WINDOW_TITLE  # still recognizably ours


class TestMain:
    def _run_main(self, tmp_path, monkeypatch) -> tuple[Path, FakeOverlay]:
        progress_file = tmp_path / "shutdown_progress.txt"
        built: list[FakeOverlay] = []

        def build(*args, **kwargs):
            built.append(FakeOverlay(*args, **kwargs))
            return built[-1]

        monkeypatch.setattr(closing_screen, "OverlayWindow", build)
        monkeypatch.setattr(sys, "argv", ["closing_screen", str(progress_file)])

        closing_screen.main()
        return progress_file, built[0]

    def test_covers_the_progress_file_it_was_given(self, tmp_path, monkeypatch):
        progress_file, overlay = self._run_main(tmp_path, monkeypatch)

        assert overlay.progress_file == progress_file
        assert overlay.kwargs["title"] == WINDOW_TITLE

    def test_reports_ready_only_once_the_cover_is_painted(self, tmp_path, monkeypatch):
        """Teardown holds its first kill for this flag, so writing it any earlier
        than the paint would hand back a promise the screen has not kept."""
        progress_file, overlay = self._run_main(tmp_path, monkeypatch)

        assert not ready_file_for(progress_file).exists()

        overlay.on_shown()

        assert ready_file_for(progress_file).exists()
