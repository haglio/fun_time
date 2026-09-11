from __future__ import annotations

from pathlib import Path

from player_core.session_quit import SESSION_QUIT

from main_player.dashboard import Dashboard


class TestAskingForSomething:
    def test_the_ask_goes_out_on_the_dashboards_channel(self, tmp_path: Path):
        dashboard = Dashboard(tmp_path / "dashboard_cmd.txt")

        dashboard.post("audio_mute")

        assert (tmp_path / "dashboard_cmd.txt").read_text(
            encoding="utf-8").split() == ["audio_mute"]

    def test_a_second_ask_joins_the_queue_rather_than_replacing_it(self, tmp_path: Path):
        """That file carries every mouse- and voice-driven writer at once and
        the dispatch loop drains it a tick at a time, so an ask that overwrote
        it would drop whatever was still waiting."""
        dashboard = Dashboard(tmp_path / "dashboard_cmd.txt")

        dashboard.post("main_next")
        dashboard.post("audio_set_volume|40")

        assert (tmp_path / "dashboard_cmd.txt").read_text(encoding="utf-8").split() == [
            "main_next", "audio_set_volume|40"]

    def test_with_no_channel_there_is_nobody_to_ask(self):
        """A player launched by hand, or by a test.  Every control on this HUD
        asks rather than acts — the picture itself included — so an ask with
        nowhere to go is dropped rather than raised into a run loop that has a
        frame to draw, as a quit gesture with nowhere to go already was."""
        Dashboard(None).post("audio_mute")


class TestAQuitGesture:
    """The close button, Alt+F4, Ctrl+Q -- every way this window is told to go."""

    def test_in_a_session_it_asks_and_this_player_keeps_running(self, tmp_path: Path):
        """The main player stays up until the teardown reaches it, so the closing cover is
        what the user watches rather than the main slot emptying ahead of
        everything else."""
        cmd_file = tmp_path / "dashboard_cmd.txt"

        Dashboard(cmd_file).take_quit_gesture()

        assert cmd_file.read_text(encoding="utf-8").split() == [SESSION_QUIT]
