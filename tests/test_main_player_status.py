"""The lines Kino adds to the Main Funestra's status file."""
from __future__ import annotations

from player_core.modes import LengthMode, LoopState

from main_player.status import LibraryStatus, status_fields


class StubLoops:
    def __init__(self, state: LoopState = LoopState.NORMAL, bounds=None) -> None:
        self.state = state
        self.bounds = bounds


class TestStatusFields:
    def test_key_order_is_the_published_file_order(self):
        assert list(status_fields(StubLoops())) == [
            "loop_state", "loop_in_ms", "loop_out_ms",
            "length_mode", "compilation", "has_compilation", "has_other_versions", "jump_to",
        ]

    def test_a_running_loop_publishes_the_range_it_holds(self):
        """The loop is the one thing a restart cannot rebuild from the playlist:
        it is a range inside one video, so the orchestrator can only hand it
        back if it is told what it was."""
        fields = status_fields(StubLoops(LoopState.LOOPING, (2000, 4000)))

        assert fields["loop_state"] == "looping"
        assert (fields["loop_in_ms"], fields["loop_out_ms"]) == ("2000", "4000")

    def test_no_loop_publishes_an_empty_range(self):
        fields = status_fields(StubLoops())

        assert fields["loop_state"] == "normal"
        assert (fields["loop_in_ms"], fields["loop_out_ms"]) == ("0", "0")

    def test_a_mark_in_progress_is_said_without_a_range(self):
        fields = status_fields(StubLoops(LoopState.RECORDING))

        assert fields["loop_state"] == "recording"
        assert (fields["loop_in_ms"], fields["loop_out_ms"]) == ("0", "0")

    def test_the_videos_place_in_the_library_is_published_for_the_consoles_buttons(self):
        """Fun Time lights the compilation, version and clip-jump buttons, and
        draws the length pair, from these lines -- only Kino knows them, and a
        player that says nothing leaves them dim."""
        library = LibraryStatus(length_mode=LengthMode.FULL, compilation="Vol 3",
                                has_compilation=True, has_other_versions=True, jump_to="clip")

        fields = status_fields(StubLoops(), library)
        unsaid = status_fields(StubLoops())

        assert [fields[key] for key in ("length_mode", "compilation", "has_compilation",
                                        "has_other_versions", "jump_to")] == [
            "full", "Vol 3", "1", "1", "clip"]
        assert [unsaid[key] for key in ("length_mode", "compilation", "has_compilation",
                                        "has_other_versions", "jump_to")] == [
            "", "", "0", "0", ""]
