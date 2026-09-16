from __future__ import annotations

from satellite.status import status_fields
from tests.satellite_fakes import make_satellite_session


class TestStatusFields:
    def test_publishes_every_key_the_dispatch_loop_reads(self, tmp_path):
        session, player = make_satellite_session(tmp_path)
        player.position_ms = 1_500.0
        session.set_locked(True)

        fields = status_fields(session)

        assert fields["video"] == str(tmp_path / "v0.mp4")
        assert fields["position_ms"] == "1500"
        assert fields["duration_ms"] == "5000"
        assert fields["paused"] == "0"
        assert fields["locked"] == "1"

    def test_publishes_how_many_clips_a_discard_left_in_the_playlist(self, tmp_path):
        session, _player = make_satellite_session(tmp_path, entries=2)

        session.discard()

        assert status_fields(session)["playlist_length"] == "1"

    def test_key_order_is_the_published_file_order(self):
        # The dispatch loop parses key=value lines, but the file's shape is this
        # player's contract; pinning the order keeps a reordering from passing
        # silently now that the writing itself lives in player_core.
        class Stub:
            current_video = "v0.mp4"
            position_ms = 0.0
            duration_ms = 0.0
            is_paused = False
            is_locked = False
            playlist_length = 1
            speed = 1.0

        assert list(status_fields(Stub())) == [
            "video", "position_ms", "duration_ms", "paused", "locked",
            "speed", "picture", "playlist_length",
        ]

    def test_the_rate_the_satellite_plays_at_is_published(self, tmp_path):
        session, _player = make_satellite_session(tmp_path)
        session.set_speed(1.5)

        assert status_fields(session)["speed"] == "1.5"

    def test_the_six_every_player_leads_with_read_back_as_the_familys_record(self, tmp_path):
        from player_core.status import PlayerStatus, parse_status

        session, player = make_satellite_session(tmp_path)
        player.position_ms = 1_500.0

        assert parse_status(status_fields(session)) == PlayerStatus(
            video=str(tmp_path / "v0.mp4"), position_ms=1500, duration_ms=5000)

    def test_flags_follow_the_session(self, tmp_path):
        session, _player = make_satellite_session(tmp_path)
        session.set_paused(True)

        fields = status_fields(session)

        assert fields["paused"] == "1"
        assert fields["locked"] == "0"
