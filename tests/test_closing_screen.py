from __future__ import annotations

import sys
from pathlib import Path

from fun_time import closing_screen, loading_screen, preview_marker
from fun_time.closing_screen import WINDOW_TITLE
from fun_time.overlay_progress import ready_file_for

DASHBOARDS_EXACT_TITLE = preview_marker.app_title(None)


class FakeOverlay:
    def __init__(self, progress_file, **kwargs):
        self.progress_file = progress_file
        self.kwargs = kwargs
        self.on_shown = None

    def run(self, on_shown=None):
        self.on_shown = on_shown


class TestWindowTitle:
    def test_the_dispatch_loops_search_for_the_dashboard_never_finds_it(self):
        assert WINDOW_TITLE != DASHBOARDS_EXACT_TITLE
        assert DASHBOARDS_EXACT_TITLE in WINDOW_TITLE


def test_it_waits_for_news_far_less_long_than_startups_loading_screen():
    assert closing_screen.STALE_TIMEOUT_S * 2 < loading_screen.STALE_TIMEOUT_S


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
        (the_one_panel,) = built
        return progress_file, the_one_panel

    def test_the_session_ends_under_one_panel_over_every_monitor(self, tmp_path, monkeypatch):
        _progress_file, the_one_panel = self._run_main(tmp_path, monkeypatch)

        assert isinstance(the_one_panel, FakeOverlay)

    def test_covers_the_progress_file_it_was_given(self, tmp_path, monkeypatch):
        progress_file, overlay = self._run_main(tmp_path, monkeypatch)

        assert overlay.progress_file == progress_file
        assert overlay.kwargs["title"] == WINDOW_TITLE

    def test_takes_over_from_no_launchers_loading_screen_and_leaves_it_nothing(self, tmp_path, monkeypatch):
        _progress_file, overlay = self._run_main(tmp_path, monkeypatch)

        assert overlay.kwargs.get("taking_over") is None

    def test_teardown_is_told_it_is_up_only_once_it_is_painted(self, tmp_path, monkeypatch):
        progress_file, overlay = self._run_main(tmp_path, monkeypatch)

        assert not ready_file_for(progress_file).exists()

        overlay.on_shown()

        assert ready_file_for(progress_file).exists()
