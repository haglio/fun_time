from __future__ import annotations

import json

from player_core.playback_rate import MAX_RATE, MIN_RATE

from main_player.play_points import PlayPoints
from main_player.seeking import GIVE_UP_AFTER
from tests.satellite_fakes import make_satellite_session as _make_session


class TestLoadAndPlay:
    def test_init_loads_first_entry_and_plays(self, tmp_path):
        session, player = _make_session(tmp_path)

        assert player.opened == [tmp_path / "v0.mp4"]
        assert player.paused is False
        assert session.current_video == tmp_path / "v0.mp4"

    def test_init_start_paused_tells_player_to_pause(self, tmp_path):
        session, player = _make_session(tmp_path, start_paused=True)

        assert session.is_paused
        assert player.paused is True


class TestNavigation:
    def test_step_advances_and_wraps(self, tmp_path):
        session, player = _make_session(tmp_path, entries=2)

        session.step(1)
        assert player.opened[-1] == tmp_path / "v1.mp4"
        assert session.current_video == tmp_path / "v1.mp4"

        session.step(1)
        assert player.opened[-1] == tmp_path / "v0.mp4"  # wraps forward
        assert session.current_video == tmp_path / "v0.mp4"

        session.step(-1)
        assert player.opened[-1] == tmp_path / "v1.mp4"  # wraps backward


class TestSeeking:
    def test_seek_to_reaches_the_player(self, tmp_path):
        session, player = _make_session(tmp_path)

        session.seek_to(1_500.0)

        assert player.seeks == [1_500.0]

    def test_a_seek_leaves_the_playlist_and_the_prefetch_where_they_are(self, tmp_path):
        # A scrub is about where you are inside one clip: nothing about the queue
        # moves, so the staged next is still the one mpv will roll onto.
        session, player = _make_session(tmp_path, entries=3)

        session.seek_to(2_000.0)

        assert session.current_video == tmp_path / "v0.mp4"
        assert player.staged_next == tmp_path / "v1.mp4"
        assert player.opened == [tmp_path / "v0.mp4"]      # no cold reload


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

        assert session.current_video == tmp_path / "v1.mp4"

    def test_advance_before_eof_is_a_noop(self, tmp_path):
        session, _player = _make_session(tmp_path, entries=2)

        session.advance()

        assert session.current_video == tmp_path / "v0.mp4"

    def test_advance_while_paused_never_steps(self, tmp_path):
        # The OmniPause guarantee: a paused satellite cannot walk its playlist,
        # so there is no VLC-style "resumed on its own" storm to police.
        session, player = _make_session(tmp_path, entries=2)
        session.set_paused(True)
        player.simulate_eof_advance()

        session.advance()

        assert session.current_video == tmp_path / "v0.mp4"


class TestLock:
    def test_locked_satellite_repeats_the_same_clip_at_eof(self, tmp_path):
        session, player = _make_session(tmp_path, entries=2)
        session.set_locked(True)
        assert session.is_locked

        player.simulate_eof_advance()
        session.advance()

        assert session.current_video == tmp_path / "v0.mp4"  # repeat-one: stays on the locked clip

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

        assert session.current_video == tmp_path / "v1.mp4"


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


class TestVersions:
    """Another rendition of the clip on screen, put up in the clip's own place:
    the list, and everything Fun Time knows the clip by, stay as they are."""

    @staticmethod
    def _other_version(tmp_path, session):
        other = tmp_path / f"{session.current_video.stem}_sorted.mp4"
        other.write_text("fake")
        return other

    def test_a_step_plays_the_other_version_in_the_clips_own_place(self, tmp_path):
        session, player = _make_session(tmp_path, entries=2)
        clip = session.current_video
        other = self._other_version(tmp_path, session)

        session.step_version([clip, other], 1)

        assert player.opened[-1] == other
        assert (session.current_video, session.showing) == (clip, other)
        assert session.playlist == [clip, tmp_path / "v1.mp4"]

    def test_a_step_back_from_the_clips_own_file_wraps_to_the_last_version(self, tmp_path):
        session, player = _make_session(tmp_path)
        clip = session.current_video
        other = self._other_version(tmp_path, session)

        session.step_version([clip, other], -1)

        assert session.showing == other

    def test_the_hud_names_the_clip_and_the_file_from_the_first_step(self, tmp_path):
        session, _player = _make_session(tmp_path)
        clip = session.current_video
        other = self._other_version(tmp_path, session)
        named = [session.name_on_screen]

        for _ in range(2):
            session.step_version([clip, other], 1)
            named.append(session.name_on_screen)

        assert named == ["v0", f"v0 ({other.name})", f"v0 ({clip.name})"]

    def test_a_clip_come_back_to_names_its_file_only_off_its_own(self, tmp_path):
        session, _player = _make_session(tmp_path, entries=2)
        clip = session.current_video
        other = self._other_version(tmp_path, session)
        named = []

        for _ in range(2):
            session.step_version([clip, other], 1)
            session.step(1)
            session.step(-1)
            named.append(session.name_on_screen)

        assert named == [f"v0 ({other.name})", "v0"]

    def test_a_rebuilt_playlist_leaves_every_clip_but_the_one_playing_on_its_own_file(
            self, tmp_path):
        session, _player = _make_session(tmp_path, entries=2)
        first, second = session.playlist
        other_first = tmp_path / "v0_sorted.mp4"
        other_second = tmp_path / "v1_sorted.mp4"
        for path in (other_first, other_second):
            path.write_text("fake")
        session.step_version([first, other_first], 1)
        session.step(1)
        session.step_version([second, other_second], 1)

        session.replace_playlist([first, second])

        assert session.showing == other_second
        session.step(1)
        assert session.showing == first

    def test_a_discarded_clip_takes_its_version_with_it(self, tmp_path):
        session, _player = _make_session(tmp_path, entries=2)
        clip = session.current_video
        other = self._other_version(tmp_path, session)
        session.step_version([clip, other], 1)

        session.discard()
        session.play_file(clip)

        assert session.showing == clip

    def test_a_family_the_file_on_screen_is_not_in_is_left_alone(self, tmp_path):
        """The step carries the versions of the clip Fun Time last saw playing,
        which an auto-advancing satellite may have moved on from."""
        session, player = _make_session(tmp_path, entries=2)
        opened = len(player.opened)
        stale = tmp_path / "somebody else.mp4"

        session.step_version([stale, tmp_path / "somebody else_sorted.mp4"], 1)

        assert len(player.opened) == opened
        assert session.showing == session.current_video


def _script(path, *actions):
    path.write_text(json.dumps({"actions": [{"at": at, "pos": pos} for at, pos in actions]}),
                    encoding="utf-8")
    return path


class TestTheScriptOfTheClipOnScreen:
    def test_a_clip_brings_the_funscript_its_playlist_line_named(self, tmp_path):
        script = _script(tmp_path / "v1.funscript", (0, 0), (400, 90))
        session, _player = _make_session(tmp_path, entries=2, funscripts={1: script})

        assert session.current_funscript is None
        session.step(1)
        assert session.current_funscript.actions == [(0, 0), (400, 90)]

    def test_a_clip_played_from_outside_the_playlist_brings_its_script(self, tmp_path):
        session, _player = _make_session(tmp_path)
        newcomer = tmp_path / "brought_back.mp4"
        newcomer.write_text("fake")
        script = _script(tmp_path / "brought_back.funscript", (0, 10), (300, 70))

        session.play_file(newcomer, script)

        assert session.current_funscript.actions == [(0, 10), (300, 70)]

    def test_a_rebuilt_playlist_brings_the_scripts_it_was_written_with(self, tmp_path):
        session, _player = _make_session(
            tmp_path, entries=2, funscripts={0: _script(tmp_path / "v0.funscript", (0, 0), (100, 99))})
        clip = tmp_path / "v0.mp4"
        rescripted = _script(tmp_path / "v0 again.funscript", (0, 50), (200, 60))

        session.replace_playlist([clip, tmp_path / "v1.mp4"], {clip: rescripted})

        assert session.current_funscript.actions == [(0, 50), (200, 60)]


class TestPlayFile:
    def test_play_file_jumps_to_a_playlist_item(self, tmp_path):
        session, player = _make_session(tmp_path, entries=3)  # on v0

        session.play_file(tmp_path / "v2.mp4")

        assert session.current_video == tmp_path / "v2.mp4"
        assert player.opened[-1] == tmp_path / "v2.mp4"
        assert len(session.playlist) == 3  # an item jump does not grow the list

    def test_play_file_inserts_a_newcomer_after_current_and_plays_it(self, tmp_path):
        session, player = _make_session(tmp_path, entries=2)  # [v0, v1] on v0
        newcomer = tmp_path / "brought_back.mp4"
        newcomer.write_text("fake")

        session.play_file(newcomer)

        assert session.current_video == newcomer
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
        assert player.opened == opened_before  # keeps playing, no reload flicker
        assert player.staged_next == y  # but the prefetched next is refreshed

    def test_replace_playlist_restarts_when_the_current_clip_is_gone(self, tmp_path):
        session, player = _make_session(tmp_path, entries=3)
        session.step(1)  # on v1
        x = tmp_path / "x.mp4"; x.write_text("fake")
        y = tmp_path / "y.mp4"; y.write_text("fake")

        session.replace_playlist([x, y])

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


class TestSpeed:
    def test_a_satellite_opens_at_normal_speed(self, tmp_path):
        session, _player = _make_session(tmp_path)

        assert session.speed == 1.0

    def test_a_rate_set_on_the_satellite_reaches_its_player(self, tmp_path):
        session, player = _make_session(tmp_path)

        session.set_speed(1.5)

        assert session.speed == player.speed == 1.5

    def test_a_rate_past_either_end_is_held_at_that_end(self, tmp_path):
        session, player = _make_session(tmp_path)

        session.set_speed(9.0)
        assert session.speed == player.speed == MAX_RATE

        session.set_speed(0.01)
        assert session.speed == player.speed == MIN_RATE


class TestClose:
    def test_close_tears_down_the_player(self, tmp_path):
        session, player = _make_session(tmp_path)

        session.close()

        assert player.closed is True


def _watch_to(session, player, position_ms):
    """Two ticks at *position_ms*: the jump onto it, then playing on from it."""
    player.position_ms = position_ms
    session.advance()
    session.advance()


class TestWhereAClipWasLeft:
    def test_a_clip_left_in_the_middle_opens_there_again(self, tmp_path):
        file = tmp_path / "points.json"
        session, player = _make_session(tmp_path, entries=2, play_points=PlayPoints(file))
        _watch_to(session, player, 2_000)

        session.step(1)
        session.step(-1)
        session.advance()

        assert player.seeks[-1] == 2_000

    def test_a_spot_mpv_will_not_take_yet_is_asked_for_again(self, tmp_path):
        """mpv refuses a seek until the clip it is opening plays, which a known
        duration does not prove -- and the refusal used to end the player."""
        file = tmp_path / "points.json"
        session, player = _make_session(tmp_path, entries=2, play_points=PlayPoints(file))
        _watch_to(session, player, 2_000)
        session.step(1)
        session.step(-1)
        player.refuse_seeks(1)

        session.advance()
        session.advance()

        assert player.seeks[-1] == 2_000

    def test_a_spot_mpv_never_takes_is_let_go_of(self, tmp_path):
        file = tmp_path / "points.json"
        session, player = _make_session(tmp_path, entries=2, play_points=PlayPoints(file))
        _watch_to(session, player, 2_000)
        session.step(1)
        session.step(-1)
        player.refuse_seeks(10 * GIVE_UP_AFTER)

        for _ in range(2 * GIVE_UP_AFTER):
            session.advance()

        assert player.refused == GIVE_UP_AFTER

    def test_leaving_a_clip_writes_down_the_very_spot(self, tmp_path):
        file = tmp_path / "points.json"
        session, player = _make_session(tmp_path, entries=2, play_points=PlayPoints(file))
        _watch_to(session, player, 2_000)
        player.position_ms = 2_048
        session.advance()

        session.step(1)

        assert PlayPoints(file).point_for(tmp_path / "v0.mp4") == 2_048

    def test_a_clip_that_played_itself_out_is_not_remembered_at_its_end(self, tmp_path):
        file = tmp_path / "points.json"
        session, player = _make_session(tmp_path, entries=2, play_points=PlayPoints(file))
        _watch_to(session, player, 4_960)

        player.simulate_eof_advance()
        session.advance()

        assert PlayPoints(file).point_for(tmp_path / "v0.mp4") == 0

    def test_a_clip_rolled_onto_is_opened_where_it_was_left(self, tmp_path):
        file = tmp_path / "points.json"
        session, player = _make_session(tmp_path, entries=2, play_points=PlayPoints(file))
        session.step(1)
        _watch_to(session, player, 2_000)
        session.step(-1)

        player.simulate_eof_advance()
        session.advance()
        session.advance()

        assert player.seeks[-1] == 2_000

    def test_closing_the_player_writes_down_the_very_spot(self, tmp_path):
        file = tmp_path / "points.json"
        session, player = _make_session(tmp_path, play_points=PlayPoints(file))
        _watch_to(session, player, 2_000)
        player.position_ms = 2_048
        session.advance()

        session.close()

        assert PlayPoints(file).point_for(tmp_path / "v0.mp4") == 2_048
