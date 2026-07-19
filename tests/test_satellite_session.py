from __future__ import annotations

from satellite.session import SatelliteSession
from tests.satellite_fakes import FakeSatellitePlayer


def _make_session(tmp_path, *, entries=1, start_paused=False, duration_ms=5_000.0):
    playlist = []
    for i in range(entries):
        vid = tmp_path / f"v{i}.mp4"
        vid.write_text("fake")
        playlist.append(vid)
    player = FakeSatellitePlayer(duration_ms=duration_ms)
    session = SatelliteSession(playlist, player=player, start_paused=start_paused)
    return session, player


class TestLoadAndPlay:
    def test_init_loads_first_entry_and_plays(self, tmp_path):
        session, player = _make_session(tmp_path)

        assert player.opened == [tmp_path / "v0.mp4"]
        assert player.paused is False
        assert session.current_video == tmp_path / "v0.mp4"
        assert session.index == 0

    def test_init_start_paused_tells_player_to_pause(self, tmp_path):
        session, player = _make_session(tmp_path, start_paused=True)

        assert session.is_paused
        assert player.paused is True


class TestNavigation:
    def test_step_advances_and_wraps(self, tmp_path):
        session, player = _make_session(tmp_path, entries=2)

        session.step(1)
        assert player.opened[-1] == tmp_path / "v1.mp4"
        assert session.index == 1

        session.step(1)
        assert player.opened[-1] == tmp_path / "v0.mp4"  # wraps forward
        assert session.index == 0

        session.step(-1)
        assert player.opened[-1] == tmp_path / "v1.mp4"  # wraps backward


class TestPause:
    def test_set_paused_drives_the_player(self, tmp_path):
        session, player = _make_session(tmp_path)

        session.set_paused(True)
        assert session.is_paused
        assert player.paused is True

        session.set_paused(False)
        assert not session.is_paused
        assert player.paused is False


class TestPrefetch:
    def test_init_stages_the_next_clip(self, tmp_path):
        # The upcoming clip is handed to mpv straight away so prefetch can open it
        # before it is needed.
        _session, player = _make_session(tmp_path, entries=3)

        assert player.staged_next == tmp_path / "v1.mp4"

    def test_single_clip_stages_itself(self, tmp_path):
        # A lone clip wraps onto itself, so the staged next is the same file — the
        # advance replays it gaplessly instead of cold-reloading.
        _session, player = _make_session(tmp_path, entries=1)

        assert player.staged_next == tmp_path / "v0.mp4"

    def test_step_restages_the_new_next(self, tmp_path):
        session, player = _make_session(tmp_path, entries=3)

        session.step(1)  # on v1

        assert player.staged_next == tmp_path / "v2.mp4"

    def test_auto_advance_is_seamless_and_does_not_reload(self, tmp_path):
        session, player = _make_session(tmp_path, entries=3)

        # mpv reaches end-of-file and rolls onto the prefetched next on its own.
        player.simulate_eof_advance()
        session.advance()

        assert session.index == 1
        assert session.current_video == tmp_path / "v1.mp4"
        # The whole point: the next clip was NOT cold-opened on screen...
        assert player.opened == [tmp_path / "v0.mp4"]
        # ...and the clip after it is now prefetched.
        assert player.staged_next == tmp_path / "v2.mp4"


class TestAdvance:
    def test_advance_after_eof_steps_to_next(self, tmp_path):
        session, player = _make_session(tmp_path, entries=2)
        player.simulate_eof_advance()

        session.advance()

        assert session.index == 1

    def test_advance_before_eof_is_a_noop(self, tmp_path):
        session, _player = _make_session(tmp_path, entries=2)

        session.advance()

        assert session.index == 0

    def test_advance_while_paused_never_steps(self, tmp_path):
        # The OmniPause guarantee: a paused satellite cannot walk its playlist,
        # so there is no VLC-style "resumed on its own" storm to police.
        session, player = _make_session(tmp_path, entries=2)
        session.set_paused(True)
        player.simulate_eof_advance()

        session.advance()

        assert session.index == 0


class TestLock:
    def test_locked_satellite_repeats_the_same_clip_at_eof(self, tmp_path):
        session, player = _make_session(tmp_path, entries=2)
        session.set_locked(True)
        assert session.is_locked

        player.simulate_eof_advance()
        session.advance()

        assert session.index == 0  # repeat-one: stays on the locked clip

    def test_lock_engages_native_loop_and_clears_the_prefetch(self, tmp_path):
        # mpv loops the file itself (loop_file=inf) so a locked short clip repeats
        # without a reload flicker, and the staged next is dropped so advance
        # cannot roll off it.
        session, player = _make_session(tmp_path, entries=2)

        session.set_locked(True)
        assert player.loop_file is True
        assert player.staged_next is None

        session.set_locked(False)
        assert player.loop_file is False
        assert player.staged_next == tmp_path / "v1.mp4"  # prefetch resumes

    def test_unlocked_satellite_advances_again(self, tmp_path):
        session, player = _make_session(tmp_path, entries=2)
        session.set_locked(True)
        session.set_locked(False)

        player.simulate_eof_advance()
        session.advance()

        assert session.index == 1


class TestDiscard:
    def test_discard_removes_current_and_plays_next(self, tmp_path):
        session, player = _make_session(tmp_path, entries=3)  # on v0

        session.discard()

        assert session.current_video == tmp_path / "v1.mp4"
        assert [p.name for p in session.playlist] == ["v1.mp4", "v2.mp4"]
        assert player.opened[-1] == tmp_path / "v1.mp4"

    def test_discard_on_the_last_entry_wraps_to_the_first(self, tmp_path):
        session, _player = _make_session(tmp_path, entries=3)
        session.step(2)  # on v2, the last

        session.discard()

        assert session.current_video == tmp_path / "v0.mp4"
        assert [p.name for p in session.playlist] == ["v0.mp4", "v1.mp4"]

    def test_discard_of_the_only_clip_is_a_noop(self, tmp_path):
        # A satellite must never be left with an empty playlist; the last clip
        # cannot be discarded.
        session, player = _make_session(tmp_path, entries=1)
        opened_before = list(player.opened)

        session.discard()

        assert len(session.playlist) == 1
        assert player.opened == opened_before


class TestPlayFile:
    def test_play_file_jumps_to_a_playlist_member(self, tmp_path):
        session, player = _make_session(tmp_path, entries=3)  # on v0

        session.play_file(tmp_path / "v2.mp4")

        assert session.index == 2
        assert session.current_video == tmp_path / "v2.mp4"
        assert player.opened[-1] == tmp_path / "v2.mp4"
        assert len(session.playlist) == 3  # a member jump does not grow the list

    def test_play_file_inserts_a_newcomer_after_current_and_plays_it(self, tmp_path):
        session, player = _make_session(tmp_path, entries=2)  # [v0, v1] on v0
        newcomer = tmp_path / "brought_back.mp4"
        newcomer.write_text("fake")

        session.play_file(newcomer)

        assert session.current_video == newcomer
        assert session.index == 1
        assert [p.name for p in session.playlist] == ["v0.mp4", "brought_back.mp4", "v1.mp4"]
        assert player.opened[-1] == newcomer


class TestPlaylistReplacement:
    def test_replace_playlist_keeps_the_current_clip_when_it_survives(self, tmp_path):
        # Reloading a rebuilt playlist (e.g. an F-mode toggle) should not
        # interrupt the clip you are watching if it is still in the new list.
        session, player = _make_session(tmp_path, entries=3)
        session.step(1)  # on v1
        opened_before = list(player.opened)
        x = tmp_path / "x.mp4"; x.write_text("fake")
        y = tmp_path / "y.mp4"; y.write_text("fake")

        session.replace_playlist([x, tmp_path / "v1.mp4", y])

        assert session.current_video == tmp_path / "v1.mp4"
        assert session.index == 1
        assert player.opened == opened_before  # keeps playing, no reload flicker
        assert player.staged_next == y  # but the prefetched next is refreshed

    def test_replace_playlist_restarts_when_the_current_clip_is_gone(self, tmp_path):
        session, player = _make_session(tmp_path, entries=3)
        session.step(1)  # on v1
        x = tmp_path / "x.mp4"; x.write_text("fake")
        y = tmp_path / "y.mp4"; y.write_text("fake")

        session.replace_playlist([x, y])

        assert session.index == 0
        assert session.current_video == x
        assert player.opened[-1] == x


class TestPlaybackClock:
    def test_position_and_duration_delegate_to_the_player(self, tmp_path):
        # The status the HUD and watch-sampler read comes off the session, which
        # forwards the live clock from the player.
        session, player = _make_session(tmp_path, duration_ms=8_000.0)
        player.position_ms = 3_200.0

        assert session.position_ms == 3_200.0
        assert session.duration_ms == 8_000.0


class TestClose:
    def test_close_tears_down_the_player(self, tmp_path):
        session, player = _make_session(tmp_path)

        session.close()

        assert player.closed is True
