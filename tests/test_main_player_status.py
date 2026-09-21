from __future__ import annotations

from pathlib import Path

from player_core.modes import LengthMode
from player_core.status import PlayerStatus, parse_status

from main_player.status import LibraryStatus, status_fields


class StubSession:
    def __init__(self) -> None:
        self.current_video = Path("C:/vids/clip.mp4")
        self.position_ms = 12345.6
        self.duration_ms = 60000.0
        self.has_funscript = True
        self.funscript_resting = False
        self.loop_state = "normal"
        self.loop_bounds = None
        self.is_paused = False
        self.locked = True
        self.speed = 1.0
        self.showing_picture = False


class TestStatusFields:
    def test_a_main_player_showing_a_picture_says_so(self):
        session = StubSession()
        session.showing_picture = True

        assert status_fields(session, None)["picture"] == "1"

    def test_publishes_every_key_fun_time_reads(self):
        fields = status_fields(StubSession(), None)

        assert fields["video"] in ("C:\\vids\\clip.mp4", "C:/vids/clip.mp4")
        assert fields["position_ms"] == "12345"
        assert fields["duration_ms"] == "60000"
        assert fields["has_funscript"] == "1"
        assert fields["funscript_resting"] == "0"
        assert fields["loop_state"] == "normal"
        assert fields["paused"] == "0"
        assert fields["locked"] == "1"

    def test_key_order_is_the_published_file_order(self):
        # fun_time parses key=value lines, but the file's shape is the main player's
        # contract; pinning the order keeps a reordering from passing silently.
        # The family's seven lead, then the main player's own six: handoff_touch_ms
        # is read by fun_time's dashboard runtime and its dispatch loop, and while
        # it was composed in a closure inside main_player.app's run loop this list
        # said ten and nothing noticed.
        assert list(status_fields(StubSession(), None)) == [
            "video", "position_ms", "duration_ms", "paused", "locked", "speed", "picture",
            "has_funscript", "funscript_resting", "loop_state",
            "loop_in_ms", "loop_out_ms", "handoff_touch_ms",
            "length_mode", "compilation", "has_compilation", "has_other_versions", "jump_to",
        ]

    def test_the_seven_every_player_leads_with_read_back_as_the_familys_record(self):
        assert parse_status(status_fields(StubSession(), None)) == PlayerStatus(
            video=str(Path("C:/vids/clip.mp4")), position_ms=12345, duration_ms=60000, locked=True)

    def test_the_chosen_touch_is_published_as_whole_milliseconds(self):
        assert status_fields(StubSession(), 4200.7)["handoff_touch_ms"] == "4200"

    def test_no_chosen_touch_publishes_an_empty_field_rather_than_a_zero(self):
        """Zero is a real media time; an arbiter reading one would end Genau's
        turn at the top of the video."""
        assert status_fields(StubSession(), None)["handoff_touch_ms"] == ""

    def test_nothing_else_moves_when_the_touch_does(self):
        with_touch = status_fields(StubSession(), 4200)
        without = status_fields(StubSession(), None)

        assert {k: v for k, v in with_touch.items() if k != "handoff_touch_ms"} == {
            k: v for k, v in without.items() if k != "handoff_touch_ms"}

    def test_a_running_loop_publishes_the_range_it_holds(self):
        """The loop is the one thing on this player that a restart cannot
        rebuild from the playlist: it is a range inside one video, so the
        orchestrator can only hand it back if it is told what it was."""
        session = StubSession()
        session.loop_state = "looping"
        session.loop_bounds = (2000, 4000)

        fields = status_fields(session, None)

        assert (fields["loop_in_ms"], fields["loop_out_ms"]) == ("2000", "4000")

    def test_no_loop_publishes_an_empty_range(self):
        """Zeros rather than blanks, so the reader parses one shape either way —
        and an empty range is no loop, which is what it means."""
        fields = status_fields(StubSession(), None)

        assert (fields["loop_in_ms"], fields["loop_out_ms"]) == ("0", "0")

    def test_flags_follow_the_session(self):
        session = StubSession()
        session.has_funscript = False
        session.funscript_resting = True
        session.is_paused = True
        session.loop_state = "recording"
        session.locked = False

        fields = status_fields(session, None)

        assert fields["has_funscript"] == "0"
        assert fields["funscript_resting"] == "1"
        assert fields["paused"] == "1"
        assert fields["loop_state"] == "recording"
        assert fields["locked"] == "0"

    def test_playhead_is_truncated_to_whole_milliseconds(self):
        session = StubSession()
        session.position_ms = 12345.9

        assert status_fields(session, None)["position_ms"] == "12345"

    def test_the_videos_place_in_the_library_is_published_for_the_consoles_buttons(self):
        """Fun Time lights the compilation, version and clip-jump buttons, and
        draws the length pair, from these lines -- only this player knows them,
        and a player that says nothing leaves them dim."""
        library = LibraryStatus(length_mode=LengthMode.FULL, compilation="Vol 3",
                                has_compilation=True, has_other_versions=True, jump_to="clip")

        fields = status_fields(StubSession(), None, library=library)
        unsaid = status_fields(StubSession(), None)

        assert [fields[key] for key in ("length_mode", "compilation", "has_compilation",
                                        "has_other_versions", "jump_to")] == [
            "full", "Vol 3", "1", "1", "clip"]
        assert [unsaid[key] for key in ("length_mode", "compilation", "has_compilation",
                                        "has_other_versions", "jump_to")] == [
            "", "", "0", "0", ""]


def test_the_rate_the_video_plays_at_is_published_for_the_satellites_to_take():
    session = StubSession()
    session.speed = 1.25

    assert status_fields(session, None)["speed"] == "1.25"
