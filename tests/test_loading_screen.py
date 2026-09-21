from __future__ import annotations

import sys
from unittest.mock import patch

from fun_time.loading_screen import WINDOW_TITLE, main
from fun_time.overlay_progress import STARTUP_PHASES


class TestWhatTheScreenOpensOn:
    def test_it_says_what_the_launch_is_already_doing(self, tmp_path, monkeypatch):
        """The launch writes its first phase before the screen exists, so the
        screen's own opening words are that phase's: a different wording would
        read as a flicker on the first poll."""
        monkeypatch.setattr(sys, "argv", ["loading_screen", str(tmp_path / "progress.txt")])

        with patch("fun_time.overlay_window.OverlayWindow") as window:
            main()

        assert window.call_args.kwargs["status"] == STARTUP_PHASES[0].message


class TestWindowTitle:
    def test_title_cannot_be_mistaken_for_the_dashboard(self):
        """Dashboard lookups match the exact title "Fun Time"; the borderless
        loading overlay must present a different exact title so it can never
        be resolved (and z-order-managed) as the dashboard."""
        assert WINDOW_TITLE != "Fun Time"
        assert "Fun Time" in WINDOW_TITLE  # still recognizably ours
