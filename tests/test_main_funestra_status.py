from __future__ import annotations

from funestra.modes import LoopState

from main_funestra.status import LibraryStatus, status_fields

WHAT_LIGHTS_THE_MAIN_HUDS_LIBRARY_BUTTONS = (
    "compilation", "has_compilation", "has_other_versions", "jump_to")


class StubLoops:
    def __init__(self, state: LoopState = LoopState.NORMAL, bounds=None) -> None:
        self.state = state
        self.bounds = bounds


class TestStatusFields:
    def test_key_order_is_the_published_file_order(self):
        assert list(status_fields(StubLoops())) == [
            "loop_state", "loop_in_ms", "loop_out_ms",
            "compilation", "has_compilation", "has_other_versions", "jump_to",
        ]

    def test_a_running_loop_publishes_the_range_a_restart_hands_back(self):
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

    def test_what_kino_knows_of_the_video_is_published_for_the_main_huds_buttons(self):
        only_kino_knows = LibraryStatus(compilation="Vol 3", has_compilation=True,
                                        has_other_versions=True, jump_to="clip")

        fields = status_fields(StubLoops(), only_kino_knows)

        assert [fields[key] for key in WHAT_LIGHTS_THE_MAIN_HUDS_LIBRARY_BUTTONS] == [
            "Vol 3", "1", "1", "clip"]

    def test_told_nothing_by_kino_the_file_leaves_those_buttons_dim(self):
        untold = status_fields(StubLoops())

        assert [untold[key] for key in WHAT_LIGHTS_THE_MAIN_HUDS_LIBRARY_BUTTONS] == [
            "", "0", "0", ""]
