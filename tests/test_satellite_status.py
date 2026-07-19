from __future__ import annotations

from satellite.session import SatelliteSession
from satellite.status import status_fields
from tests.satellite_fakes import FakeSatellitePlayer


def _make_session(tmp_path):
    vid = tmp_path / "clip.mp4"
    vid.write_text("fake")
    return SatelliteSession([vid], player=FakeSatellitePlayer())


class TestStatusFields:
    def test_publishes_every_key_the_dispatch_loop_reads(self, tmp_path):
        session = _make_session(tmp_path)
        session._player.position_ms = 1_500.0
        session.set_locked(True)

        fields = status_fields(session)

        assert fields["video"] == str(tmp_path / "clip.mp4")
        assert fields["position_ms"] == "1500"
        assert fields["duration_ms"] == "5000"
        assert fields["paused"] == "0"
        assert fields["locked"] == "1"

    def test_key_order_is_the_published_file_order(self):
        # The dispatch loop parses key=value lines, but the file's shape is this
        # player's contract; pinning the order keeps a reordering from passing
        # silently now that the writing itself lives in player_core.
        class Stub:
            current_video = "clip.mp4"
            position_ms = 0.0
            duration_ms = 0.0
            is_paused = False
            is_locked = False

        assert list(status_fields(Stub())) == [
            "video", "position_ms", "duration_ms", "paused", "locked",
        ]

    def test_flags_follow_the_session(self, tmp_path):
        session = _make_session(tmp_path)
        session.set_paused(True)

        fields = status_fields(session)

        assert fields["paused"] == "1"
        assert fields["locked"] == "0"
