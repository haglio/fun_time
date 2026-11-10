from __future__ import annotations

import sys
from unittest.mock import patch

from shared_ui.testing import taking_over_stand_in

from fun_time.loading_screen import WINDOW_TITLE, main
from fun_time.overlay_progress import STARTUP_PHASES


class TestWhatTheScreenOpensOn:
    def test_it_opens_on_the_launchs_own_first_words_so_its_first_read_changes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["loading_screen", str(tmp_path / "progress.txt")])

        with patch("fun_time.overlay_window.OverlayWindow") as window:
            main()

        assert window.over_every_monitor.call_args.kwargs["status"] == STARTUP_PHASES[0].message


class TestTheLaunchersLoadingScreen:
    def test_the_startup_loading_screen_takes_over_from_the_one_its_launcher_showed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["loading_screen", str(tmp_path / "progress.txt")])

        with patch("fun_time.overlay_window.OverlayWindow") as window,              taking_over_stand_in() as taking_over:
            main()

        assert window.over_every_monitor.call_args.kwargs["taking_over"] is (
            taking_over.from_the_launcher.return_value)


class TestWindowTitle:
    def test_title_cannot_be_mistaken_for_the_dashboard(self):
        assert WINDOW_TITLE != "Fun Time"
        assert "Fun Time" in WINDOW_TITLE
