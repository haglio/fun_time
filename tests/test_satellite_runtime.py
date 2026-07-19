from __future__ import annotations

import threading

from satellite.runtime import apply_command
from satellite.session import SatelliteSession
from tests.satellite_fakes import FakeSatellitePlayer


def _make_session(tmp_path, *, entries=3):
    playlist = []
    for i in range(entries):
        vid = tmp_path / f"v{i}.mp4"
        vid.write_text("fake")
        playlist.append(vid)
    return SatelliteSession(playlist, player=FakeSatellitePlayer())


class TestApplyCommand:
    def test_next_and_prev_navigate(self, tmp_path):
        session = _make_session(tmp_path)
        assert apply_command("NEXT", session) is True
        assert session.index == 1
        assert apply_command("PREV", session) is True
        assert session.index == 0

    def test_lock_and_unlock_are_idempotent_verbs(self, tmp_path):
        session = _make_session(tmp_path)
        assert apply_command("LOCK", session) is True
        assert session.is_locked is True
        assert apply_command("UNLOCK", session) is True
        assert session.is_locked is False

    def test_trash_discards_the_current_clip(self, tmp_path):
        session = _make_session(tmp_path)
        assert apply_command("TRASH", session) is True
        assert [p.name for p in session.playlist] == ["v1.mp4", "v2.mp4"]

    def test_play_file_plays_the_argument_path(self, tmp_path):
        session = _make_session(tmp_path)
        assert apply_command(f"PLAY_FILE {tmp_path / 'v2.mp4'}", session) is True
        assert session.current_video == tmp_path / "v2.mp4"

    def test_keyword_is_case_insensitive(self, tmp_path):
        session = _make_session(tmp_path)
        assert apply_command("next", session) is True
        assert session.index == 1

    def test_reload_playlist_invokes_the_callback(self, tmp_path):
        session = _make_session(tmp_path)
        calls = []
        assert apply_command("RELOAD_PLAYLIST", session, reload_playlist=lambda: calls.append(1)) is True
        assert calls == [1]

    def test_quit_sets_the_stop_event(self, tmp_path):
        session = _make_session(tmp_path)
        stop = threading.Event()
        assert apply_command("QUIT", session, stop_event=stop) is True
        assert stop.is_set()

    def test_unknown_command_returns_false(self, tmp_path):
        session = _make_session(tmp_path)
        assert apply_command("FLOOP", session) is False
        assert apply_command("", session) is False
