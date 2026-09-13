from __future__ import annotations

from fun_time.loading_screen import WINDOW_TITLE


class TestWindowTitle:
    def test_title_cannot_be_mistaken_for_the_dashboard(self):
        """Dashboard lookups match the exact title "Fun Time"; the borderless
        loading overlay must present a different exact title so it can never
        be resolved (and z-order-managed) as the dashboard."""
        assert WINDOW_TITLE != "Fun Time"
        assert "Fun Time" in WINDOW_TITLE  # still recognizably ours
