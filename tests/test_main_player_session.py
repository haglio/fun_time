from __future__ import annotations

from pathlib import Path

import pytest
from player_core.funscript import Funscript
from player_core.modes import LoopState
from player_core.playback_rate import MAX_RATE, MIN_RATE
from player_core.playlist import PlaylistItem

from main_player.play_points import PlayPoints
from main_player.session import (
    MAX_VOLUME,
    MIN_VOLUME,
    PlayerSession,
)
from tests.mpv_refusals import RefusesSeeks


class FakePlayer(RefusesSeeks):
    """Stand-in for the mpv-backed player: records what the session drives."""

    def __init__(self, duration_ms: float = 60_000.0) -> None:
        self.opened: list[Path] = []
        self.duration_ms = duration_ms
        self.position_ms = 0.0
        self.eof = False
        # Constructed looping the one file, the way the main player constructs its real
        # player; a lock toggle is what moves it.
        self.loop_file = True
        self.paused = False
        self.ab_loop: tuple[float, float] | None = None
        # Every value the session pushed, so "told the same thing again" can be
        # told apart from "not told at all".
        self.pauses: list[bool] = []
        self.seeks: list[float] = []
        self.speeds: list[float] = []
        self.volumes: list[int] = []
        self.paces: list[float] = []
        self.showing_picture = False
        self.source_dims = (0, 0)
        self.closed = False

    def set_pace(self, seconds: float) -> None:
        self.paces.append(seconds)

    def load(self, path: Path) -> None:
        self.opened.append(path)
        self.position_ms = 0.0
        self.eof = False

    def set_paused(self, paused: bool) -> None:
        self.pauses.append(paused)
        self.paused = paused

    def set_loop_file(self, loop: bool) -> None:
        self.loop_file = loop

    def set_speed(self, speed: float) -> None:
        self.speeds.append(speed)

    def set_volume(self, volume: int) -> None:
        self.volumes.append(volume)

    def seek_ms(self, ms: float) -> None:
        self.refuse_if_asked()
        self.position_ms = ms
        self.seeks.append(ms)

    def set_ab_loop(self, in_ms: float, out_ms: float) -> None:
        self.ab_loop = (in_ms, out_ms)

    def clear_ab_loop(self) -> None:
        self.ab_loop = None

    def close(self) -> None:
        self.closed = True


class FakeTCode:
    def __init__(self) -> None:
        self.updates: list[tuple[int, Funscript, float]] = []
        self.parks = 0
        self.resets = 0
        self.closed = False

    def update(self, position_ms: int, fs: Funscript, *, speed: float = 1.0) -> None:
        self.updates.append((position_ms, fs, speed))

    def park(self) -> None:
        self.parks += 1

    def reset(self) -> None:
        self.resets += 1

    def close(self) -> None:
        self.closed = True


_FS_JSON = (
    '{"actions": [{"at": 0, "pos": 100}, {"at": 1000, "pos": 0},'
    ' {"at": 2000, "pos": 100}, {"at": 3000, "pos": 0}, {"at": 4000, "pos": 100}]}'
)


def _make_session(tmp_path, *, scripted=True, start_paused=False, duration_ms=60_000.0, entries=1,
                  play_points=None):
    playlist = []
    for i in range(entries):
        vid = tmp_path / f"v{i}.mp4"
        vid.write_text("fake")
        fs = None
        if scripted:
            fs = tmp_path / f"v{i}.funscript"
            fs.write_text(_FS_JSON)
        playlist.append(PlaylistItem(vid, fs))
    player = FakePlayer(duration_ms=duration_ms)
    tcode = FakeTCode()
    session = PlayerSession(
        playlist, player=player, tcode=tcode, start_paused=start_paused,
        play_points=play_points,
    )
    return session, player, tcode


class TestPictures:
    def test_the_pace_a_source_sets_reaches_the_player(self, tmp_path):
        session, player, _ = _make_session(tmp_path)

        session.set_pace(2.5)

        assert player.paces == [2.5]

    def test_the_session_shows_a_picture_when_its_player_does(self, tmp_path):
        session, player, _ = _make_session(tmp_path, scripted=False)

        player.showing_picture = True

        assert session.showing_picture is True


class TestShape:
    def test_a_video_taller_than_it_is_wide_is_portrait(self, tmp_path):
        session, player, _ = _make_session(tmp_path, scripted=False)

        player.source_dims = (1080, 1920)

        assert session.portrait is True

    def test_a_video_not_measured_yet_has_no_shape(self, tmp_path):
        session, _player, _ = _make_session(tmp_path, scripted=False)

        assert session.portrait is None


def _watch_to(session, player, position_ms):
    """Two ticks at *position_ms*: the jump onto it, then ordinary playback."""
    player.position_ms = position_ms
    session.advance()
    session.advance()


class TestFunscriptResting:
    def test_on_the_cluster_is_not_resting(self, tmp_path):
        session, player, _ = _make_session(tmp_path)  # dense action 0-4000ms
        player.position_ms = 2000.0

        assert session.funscript_resting is False

    def test_deep_in_a_gap_is_resting(self, tmp_path):
        session, player, _ = _make_session(tmp_path)
        player.position_ms = 20000.0  # long past the cluster and its buffer

        assert session.funscript_resting is True

    def test_unscripted_video_is_not_resting(self, tmp_path):
        session, player, _ = _make_session(tmp_path, scripted=False)
        player.position_ms = 20000.0

        assert session.funscript_resting is False


class TestLoadAndPlay:
    def test_init_loads_first_entry_and_plays(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)

        assert player.opened == [tmp_path / "v0.mp4"]
        assert player.paused is False
        assert session.has_funscript

    def test_init_start_paused_tells_player_to_pause(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, start_paused=True)

        assert session.is_paused
        assert player.paused is True

    def test_unscripted_entry_has_no_funscript(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, scripted=False)

        assert not session.has_funscript
        assert session.loop_state is LoopState.NORMAL

    def test_load_clears_any_previous_ab_loop(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, entries=2)
        player.ab_loop = (1.0, 2.0)

        session.step(1)

        assert player.ab_loop is None


class TestTheLoopUnderTheSession:
    """The session's share of a loop: reading the playhead off the player,
    passing the three readings back out, and the seek that survives a file
    still opening.  What the loop asks OF the player is
    `test_main_player_session_loops.py`; where the bounds go is
    `test_main_player_loop_controller.py`.
    """

    def test_the_gesture_is_marked_where_the_playhead_is(self, tmp_path):
        """Neither end of the gesture carries a position: both read the clock,
        which is the whole of what the session adds to a record."""
        session, player, tcode = _make_session(tmp_path)
        player.position_ms = 2500

        session.record_down()
        assert session.record_in_ms == 2500

        player.position_ms = 3500
        session.record_up()

        # Snapped out to the base actions at 2000 and 4000.
        assert (session.loop_state, session.loop_bounds) == ("looping", (2000, 4000))
        assert player.ab_loop == (2000, 4000)
        assert player.seeks[-1] == 2000
        assert tcode.resets >= 2, "landing on the loop's start is a clock jump"

    def test_the_three_readings_say_nothing_while_no_loop_is_running(self, tmp_path):
        session, _player, _tcode = _make_session(tmp_path)

        assert (session.loop_state, session.loop_bounds, session.record_in_ms) == (
            "normal", None, None)

    def test_cancelling_reaches_the_loop(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path)
        session.restore_loop(2000, 4000)

        session.loop_cancel()

        assert (session.loop_state, player.ab_loop) == ("normal", None)

    def test_a_seek_mpv_will_not_take_yet_is_asked_for_again(self, tmp_path):
        """mpv refuses a seek until the file it is opening plays, which a known
        duration does not prove -- and the refusal used to end the player."""
        session, player, _tcode = _make_session(tmp_path)
        player.refuse_seeks(1)

        session.seek_to(2000)
        session.advance()

        assert player.seeks[-1] == 2000

    def test_a_restored_loop_waits_for_a_file_that_is_still_opening(self, tmp_path):
        """Startup queues this before mpv has the file, and mpv reports no
        duration for a tick or two — a seek taken then would be clamped against
        a zero-length video and put the playhead back at the top."""
        session, player, tcode = _make_session(tmp_path)
        player.duration_ms = 0.0
        player.seeks.clear()

        session.restore_loop(2000, 4000)

        assert player.ab_loop == (2000, 4000), "mpv takes the range whenever it is set"
        assert player.seeks == []

        player.duration_ms = 60_000.0
        session.advance()

        assert player.seeks[-1] == 2000


class TestLock:
    """Locked, the video repeats; unlocked, the playlist plays around."""

    def test_a_session_opens_locked(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path)

        assert session.locked is True
        # Nothing is driven for it: the player is already constructed looping.
        assert player.loop_file is True

    def test_unlocking_hands_the_end_of_the_file_to_the_playlist(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path)

        session.set_locked(False)

        assert session.locked is False
        assert player.loop_file is False

    def test_toggle_flips_it_both_ways(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path)

        session.toggle_lock()
        assert (session.locked, player.loop_file) == (False, False)

        session.toggle_lock()
        assert (session.locked, player.loop_file) == (True, True)

    def test_unlocked_end_of_file_steps_to_the_next_video(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path, entries=2)
        session.set_locked(False)

        player.eof = True
        session.advance()

        assert player.opened[-1] == tmp_path / "v1.mp4"
        assert session.index == 1

    def test_the_playlist_wraps_around_at_the_bottom(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path, entries=2)
        session.set_locked(False)
        session.step(1)

        player.eof = True
        session.advance()

        assert player.opened[-1] == tmp_path / "v0.mp4"
        assert session.index == 0

    def test_a_stale_end_of_file_does_not_step_twice(self, tmp_path):
        """mpv keeps reporting end-of-file for a tick or two after the step is
        issued, because loadfile is asynchronous.  Reading that again would skip
        the video that was just opened."""
        session, player, _tcode = _make_session(tmp_path, entries=3)
        session.set_locked(False)

        player.eof = True
        session.advance()
        player.eof = True  # the real player has not caught up yet
        session.advance()

        assert session.index == 1
        assert player.opened[-1] == tmp_path / "v1.mp4"

    def test_the_next_end_of_file_steps_again(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path, entries=3)
        session.set_locked(False)

        player.eof = True
        session.advance()
        player.eof = False  # the new video is playing
        session.advance()
        player.eof = True
        session.advance()

        assert session.index == 2

    def test_a_paused_session_never_steps_off_the_end(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path, entries=2)
        session.set_locked(False)
        session.set_paused(True)

        player.eof = True
        session.advance()

        assert session.index == 0


class TestNavigation:
    def test_step_advances_and_wraps(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, entries=2)

        session.step(1)
        assert player.opened[-1] == tmp_path / "v1.mp4"
        assert session.index == 1

        session.step(1)
        assert player.opened[-1] == tmp_path / "v0.mp4"
        assert session.index == 0

        session.step(-1)
        assert player.opened[-1] == tmp_path / "v1.mp4"

    def test_seek_by_clamps_and_resets_tcode(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, duration_ms=30_000)
        player.position_ms = 5_000
        resets_before = tcode.resets

        session.seek_by(-10_000)
        assert player.position_ms == 0
        assert tcode.resets == resets_before + 1

        player.position_ms = 5_000
        session.seek_by(50_000)
        assert player.position_ms == 30_000  # clamped to duration

    def test_seek_to_absolute_clamps_and_resets(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, duration_ms=30_000)

        session.seek_to(12_345)
        assert player.position_ms == 12_345

        session.seek_to(99_999)
        assert player.position_ms == 30_000
        session.seek_to(-5)
        assert player.position_ms == 0

    def test_seek_back_while_recording_stops_at_the_start(self, tmp_path):
        # While marking a loop you can't rewind before where it started: a
        # backward seek lands on the start point rather than before it.
        session, player, tcode = _make_session(tmp_path, duration_ms=60_000)
        player.position_ms = 5_000
        session.record_down()  # loop start = 5000

        session.seek_by(-3_000)  # would land at 2000
        assert player.position_ms == 5_000  # clamped up to the start

        session.seek_to(1_000)  # click-to-seek before the start
        assert player.position_ms == 5_000


class TestSeekOverAFileOpen:
    """mpv opens a file asynchronously, so for a tick or two after a load it
    reports no duration.  A seek issued in the same breath as the load — landing
    on where a new video's scripting begins — has to survive that, or the ceiling
    would read the missing duration as "zero long" and put it back at the top."""

    def test_a_seek_before_the_duration_is_known_is_not_clamped_to_zero(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path)
        player.duration_ms = 0.0

        session.seek_to(60_000)

        assert player.seeks == []

    def test_it_lands_on_the_first_tick_the_duration_is_known(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path)
        player.duration_ms = 0.0
        session.seek_to(60_000)

        session.advance()
        assert player.seeks == []  # still opening

        player.duration_ms = 90_000.0
        session.advance()
        assert player.seeks == [60_000]

    def test_it_lands_even_while_paused(self, tmp_path):
        # The room can be frozen (omnipause, or genau mode) while the file opens,
        # and a held seek that waited it out would leave the wrong frame up for
        # as long as the pause lasted.
        session, player, _tcode = _make_session(tmp_path, start_paused=True)
        player.duration_ms = 0.0
        session.seek_to(60_000)
        player.duration_ms = 90_000.0

        session.advance()

        assert player.seeks == [60_000]

    def test_only_once(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path)
        player.duration_ms = 0.0
        session.seek_to(60_000)
        player.duration_ms = 90_000.0

        session.advance()
        session.advance()

        assert player.seeks == [60_000]

    def test_navigating_away_drops_a_seek_the_old_file_never_took(self, tmp_path):
        # The held seek was a position in the outgoing video; carrying it onto
        # whatever replaced it would drop the new one in an arbitrary spot.
        session, player, _tcode = _make_session(tmp_path, entries=2)
        player.duration_ms = 0.0
        session.seek_to(60_000)

        session.step(1)
        player.duration_ms = 90_000.0
        session.advance()

        assert player.seeks == []


class TestPause:
    def test_set_paused_tells_player(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)

        session.set_paused(True)
        assert session.is_paused
        assert player.paused is True

        session.set_paused(False)
        assert not session.is_paused
        assert player.paused is False

    def test_set_paused_same_state_is_noop(self, tmp_path):
        """Fun Time owns the paused flag through a file and re-asserts it on
        every tick, so being told what is already true is the common path
        rather than the odd one -- and it must reach neither the player nor the
        device."""
        session, player, tcode = _make_session(tmp_path)
        player.pauses.clear()
        resets_before = tcode.resets  # loading the first video takes the device

        session.set_paused(False)  # already playing

        assert session.is_paused is False
        assert player.pauses == []
        assert tcode.resets == resets_before

    def test_resuming_hands_the_device_back_rather_than_slamming_it(self, tmp_path):
        """While the video sat paused the last in-flight waypoint completed --
        the device walked on to wherever it was aimed and froze there -- and
        under OmniPause the broker may have parked it outright.  Resuming is a
        takeover from there, so the first waypoints glide."""
        session, player, tcode = _make_session(tmp_path)
        resets_before = tcode.resets

        session.set_paused(True)
        assert tcode.resets == resets_before, (
            "pausing hands the device over; it does not take it back")

        session.set_paused(False)

        assert tcode.resets == resets_before + 1


class TestAdvance:
    def test_advance_updates_tcode(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        player.position_ms = 1500

        session.advance()

        assert tcode.updates and tcode.updates[-1][0] == 1500

    def test_advance_without_funscript_parks_the_osr2(self, tmp_path):
        # An unscripted video has no waypoints to drive from, so the OSR2 rests at
        # its closest position rather than holding wherever the last video left it.
        session, player, tcode = _make_session(tmp_path, scripted=False)
        player.position_ms = 1500

        session.advance()

        assert tcode.updates == []
        assert tcode.parks == 1

    def test_advance_without_funscript_does_not_park_when_disabled(self, tmp_path):
        # In video mode, SET_TCODE_ENABLED 0 hands an unscripted video to the Robot Hand; the main player
        # must not fight it by also parking the OSR2.
        session, player, tcode = _make_session(tmp_path, scripted=False)
        session.set_tcode_enabled(False)
        player.position_ms = 1500

        session.advance()

        assert tcode.parks == 0

    def test_advance_without_funscript_does_not_park_while_paused(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, scripted=False)
        session.set_paused(True)
        player.position_ms = 1500

        session.advance()

        assert tcode.parks == 0

    def test_advance_skips_tcode_when_disabled(self, tmp_path):
        # SET_TCODE_ENABLED 0 gates output so Genau can drive the OSR2 solo in
        # video mode without the main player's funscript T-Code double-driving the broker.
        session, player, tcode = _make_session(tmp_path)
        session.set_tcode_enabled(False)
        player.position_ms = 1500

        session.advance()

        assert tcode.updates == []

    def test_advance_resumes_tcode_when_re_enabled(self, tmp_path):
        # Taking the device back (SET_TCODE_ENABLED 1) must let the main player drive its funscript
        # again, so the mute is a round-trip, not a one-way switch.
        session, player, tcode = _make_session(tmp_path)
        session.set_tcode_enabled(False)
        player.position_ms = 1500
        session.advance()
        assert tcode.updates == []

        session.set_tcode_enabled(True)
        player.position_ms = 1600
        session.advance()

        assert tcode.updates and tcode.updates[-1][0] == 1600

    def test_re_enabling_tcode_resets_the_driver_for_the_takeover(self, tmp_path):
        # SET_TCODE_ENABLED 1 is the video-mode handoff taking the device back from
        # Genau: the driver is reset like any other takeover, so its next tick
        # sends at once and with the handoff glide, instead of snapping the
        # device to a waypoint that may be milliseconds away.
        session, player, tcode = _make_session(tmp_path)
        session.set_tcode_enabled(False)
        resets_before = tcode.resets

        session.set_tcode_enabled(True)

        assert tcode.resets == resets_before + 1

    def test_enabling_tcode_already_enabled_does_not_reset(self, tmp_path):
        # Only the mute→drive edge is a takeover; repeating "1" must not keep
        # re-arming the glide under a script that is already driving.
        session, player, tcode = _make_session(tmp_path)
        resets_before = tcode.resets

        session.set_tcode_enabled(True)

        assert tcode.resets == resets_before

    def test_advance_while_paused_skips_tcode(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        session.set_paused(True)
        player.position_ms = 1500

        session.advance()

        assert tcode.updates == []

    def test_advance_resets_tcode_on_ab_loop_wrap(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        player.position_ms = 2500
        session.record_down()
        player.position_ms = 3500
        session.record_up()  # looping, last_pos tracked from here
        player.position_ms = 3900
        session.advance()
        resets_before = tcode.resets

        # mpv wraps B->A: position jumps backwards
        player.position_ms = 2000
        session.advance()

        assert tcode.resets == resets_before + 1

    def test_advance_finalizes_loop_when_recording_reaches_the_end(self, tmp_path):
        # Recording a loop to the end of the file: the loop closes at the end and
        # starts the instant the playhead reaches it — before loop-file wraps the
        # whole video back to the start, which would flash the opening frames.
        session, player, tcode = _make_session(
            tmp_path, scripted=False, duration_ms=10_000,
        )
        player.position_ms = 9_000
        session.record_down()

        player.position_ms = 9_950  # reached the end margin, still pre-wrap
        session.advance()

        assert session.loop_state is LoopState.LOOPING
        assert player.ab_loop == (9_000, 9_950)  # in..(just short of the end)
        assert player.seeks[-1] == 9_000  # jumped straight to the loop start

    def test_advance_finalizes_loop_if_recording_wraps_past_the_end(self, tmp_path):
        # Fallback: if a tick lands only after loop-file already rewound the clock
        # to the start (the near-end window was missed), the loop still closes at
        # the last position seen before the wrap rather than inverting to ~zero.
        session, player, tcode = _make_session(
            tmp_path, scripted=False, duration_ms=10_000,
        )
        player.position_ms = 9_000
        session.record_down()
        assert session.loop_state is LoopState.RECORDING

        player.position_ms = 9_800
        session.advance()  # tracks last_pos near the end of the file

        player.position_ms = 20  # EOF hit: clock rewound to the start
        session.advance()

        assert session.loop_state is LoopState.LOOPING
        assert player.ab_loop == (9_000, 9_800)  # in..end, not in..~0
        assert player.seeks[-1] == 9_000  # jumped back to the loop start

    def test_advance_backward_seek_while_recording_keeps_marking(self, tmp_path):
        # A backward seek mid-record also rewinds the clock, but it does not land
        # at the start of the file, so it must not be mistaken for an EOF wrap:
        # recording continues rather than snapping the loop shut early.
        session, player, tcode = _make_session(
            tmp_path, scripted=False, duration_ms=60_000,
        )
        player.position_ms = 30_000
        session.record_down()
        player.position_ms = 40_000
        session.advance()

        player.position_ms = 20_000  # user seeked back 20s, still marking
        session.advance()

        assert session.loop_state is LoopState.RECORDING
        assert player.ab_loop is None

    def test_advance_at_end_auto_steps_in_normal(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, entries=2)
        player.eof = True

        session.advance()

        assert session.index == 1

    def test_advance_at_end_does_not_step_while_looping(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        player.position_ms = 2500
        session.record_down()
        player.position_ms = 3500
        session.record_up()
        player.eof = True

        session.advance()

        assert session.index == 0

    def test_advance_at_end_auto_steps_unscripted(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, scripted=False, entries=2)
        player.eof = True

        session.advance()

        assert session.index == 1


class TestPassthroughs:
    def test_exposes_video_identity_and_duration(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)

        assert session.current_video == tmp_path / "v0.mp4"
        assert session.duration_ms == 60_000.0

    def test_exposes_loaded_funscript(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        assert isinstance(session.current_funscript, Funscript)

    def test_current_funscript_none_when_unscripted(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, scripted=False)
        assert session.current_funscript is None

    def test_close_closes_all_resources(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)

        session.close()

        assert tcode.closed
        assert player.closed


class TestPlayFile:
    def test_play_file_already_in_playlist_jumps_to_it(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, entries=3)
        target = tmp_path / "v2.mp4"

        session.play_file(PlaylistItem(target, tmp_path / "v2.funscript"))

        assert session.current_video == target
        assert session.index == 2

    def test_play_file_new_video_inserts_after_current(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, entries=2)
        newv = tmp_path / "picked.mp4"
        newv.write_text("x")

        session.play_file(PlaylistItem(newv))

        assert session.current_video == newv
        assert session.index == 1
        assert session.playlist[1] == (newv, None)


class TestReplacePlaylist:
    def test_replace_keeps_current_video_position(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, entries=2)
        v0, v1 = tmp_path / "v0.mp4", tmp_path / "v1.mp4"
        session.step(1)  # now on v1

        session.replace_playlist([PlaylistItem(v0), PlaylistItem(v1)])

        assert session.current_video == v1

    def test_replace_without_current_video_jumps_to_first(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        other = tmp_path / "other.mp4"
        other.write_text("x")

        session.replace_playlist([PlaylistItem(other)])

        # A filtered-out current video must not linger on screen: playback jumps
        # straight to the new list's first entry, mirroring how the satellites
        # restart at item 0 when F-mode reloads them.
        assert session.current_video == other
        assert player.opened[-1] == other


_SCENE_ONE = ("Jane Doe - scene one.mp4", "Jane Doe - scene one_topaz.mp4")


def _videos(tmp_path, *names: str) -> list[Path]:
    paths = [tmp_path / name for name in names]
    for path in paths:
        path.write_text("x")
    return paths


def _families(*families: list[Path]) -> dict[Path, list[PlaylistItem]]:
    return {
        video: [PlaylistItem(version) for version in family]
        for family in families for video in family
    }


def _playing(playlist: list[Path], version_index, player=None) -> PlayerSession:
    return PlayerSession(
        [PlaylistItem(video) for video in playlist], player=player or FakePlayer(),
        tcode=FakeTCode(), version_index=version_index,
    )


class TestCycleVersion:
    def test_it_says_whether_there_is_anything_to_cycle_to(self, tmp_path):
        """The console draws its version button dim where a press would do
        nothing, so this has to be the same question the swap itself asks."""
        big, small, solo = _videos(tmp_path, "Jane-1080p.mp4", "Jane-540.mp4", "solo.mp4")

        paired = _playing([big], _families([big, small]))
        alone = _playing([solo], _families([solo]))
        # A family the current video is mapped to without being in: Fun Time
        # writes the playlist from its own selection, so that happens.
        stranger = _playing([solo], {solo: [PlaylistItem(big), PlaylistItem(small)]})

        assert paired.has_other_versions is True
        assert alone.has_other_versions is False
        assert stranger.has_other_versions is False

    def test_singleton_group_is_noop(self, tmp_path):
        (solo,) = _videos(tmp_path, "solo.mp4")
        player = FakePlayer()
        session = _playing([solo], _families([solo]), player)
        before = list(player.opened)

        session.cycle_version()

        assert (player.opened, session.switching_versions) == (before, False)

    def test_no_version_index_is_noop(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        before = list(player.opened)
        session.cycle_version()
        assert player.opened == before

    def test_cycles_to_next_version_by_index_order(self, tmp_path):
        big, small = _videos(tmp_path, "Jane-1080p.mp4", "Jane-540.mp4")
        session = _playing([big], _families([big, small]))

        session.cycle_version()
        assert session.current_video == small

        session.cycle_version()
        assert session.current_video == big

    def test_a_family_of_three_walks_forward_and_wraps(self, tmp_path):
        """Two versions cannot say which way the cycle goes -- forward and
        backward are the same step modulo two -- and CYCLE_VERSION is a command
        Fun Time sends and a HUD control, so the order it walks a family is the
        observable behavior.

        The index orders versions largest-first, which is the order the library
        builds it in: the canonical file, then its smaller versions.
        """
        original, upscale, small = _videos(
            tmp_path, *_SCENE_ONE, "Jane Doe - scene one-540.mp4")
        session = _playing([original], _families([original, upscale, small]))

        walked = []
        for _ in range(4):
            session.cycle_version()
            walked.append(session.current_video)

        assert walked == [upscale, small, original, upscale]

    def test_a_step_back_walks_the_family_the_other_way(self, tmp_path):
        original, upscale, small = _videos(
            tmp_path, *_SCENE_ONE, "Jane Doe - scene one-540.mp4")
        session = _playing([original], _families([original, upscale, small]))

        walked = []
        for _ in range(4):
            session.cycle_version(-1)
            walked.append(session.current_video)

        assert walked == [small, upscale, original, small]

    def test_each_step_opens_the_new_file_from_the_beginning(self, tmp_path):
        """Nothing of the old one is preserved -- the versions are the same
        content at different sizes, but mpv is opening a different file."""
        original, upscale = _videos(tmp_path, *_SCENE_ONE)
        player = FakePlayer()
        session = _playing([original], _families([original, upscale]), player)
        opened_before = len(player.opened)

        session.cycle_version()

        assert player.opened[opened_before:] == [upscale]

    def test_a_version_the_index_does_not_know_is_left_alone(self, tmp_path):
        """A playlist can carry a video the index was built without -- Fun Time
        writes one from its own selection -- and cycling it must not swap in
        somebody else's family."""
        stranger, original, upscale = _videos(tmp_path, "Ann Bly - scene two.mp4", *_SCENE_ONE)
        session = _playing([stranger], {stranger: [PlaylistItem(original), PlaylistItem(upscale)]})

        session.cycle_version()

        assert session.current_video == stranger

    def test_the_default_is_the_version_the_playlist_gave_not_the_largest(self, tmp_path):
        original, upscale, other = _videos(tmp_path, *_SCENE_ONE, "Ann Bly - scene two.mp4")
        session = _playing([upscale, other], _families([original, upscale]))
        on_default = [session.on_default_version]

        session.cycle_version()
        session.step(1)
        session.step(-1)
        on_default.append(session.on_default_version)

        assert (session.current_video, on_default) == (original, [True, False])

    @pytest.mark.parametrize("take_up", ["load_playlist", "replace_playlist"])
    def test_a_new_playlist_starts_every_video_on_its_default(self, tmp_path, take_up):
        original, upscale, other = _videos(tmp_path, *_SCENE_ONE, "Ann Bly - scene two.mp4")
        session = _playing([original, other], _families([original, upscale]))
        session.cycle_version()

        getattr(session, take_up)([PlaylistItem(upscale), PlaylistItem(other)])
        session.step(1)
        session.step(-1)

        assert (session.current_video, session.on_default_version) == (upscale, True)

    def test_replaces_in_place_so_navigation_skips_the_alternate(self, tmp_path):
        # Cycling a version must not add a playlist entry: [ still steps back to
        # the previous distinct video, not the version we cycled away from.
        x, a1, a2, b = _videos(tmp_path, "x.mp4", "Jane-1080p.mp4", "Jane-540.mp4", "b.mp4")
        session = _playing([x, a1, b], _families([a1, a2]))
        session.step(1)
        assert session.current_video == a1

        session.cycle_version()
        assert session.current_video == a2
        assert session.playlist == [(x, None), (a2, None), (b, None)]

        session.step(-1)
        assert session.current_video == x


class TestLoadPlaylist:
    def test_load_playlist_jumps_to_first_of_new_list(self, tmp_path):
        session, player, tcode = _make_session(tmp_path, entries=2)
        a = tmp_path / "a.mp4"; a.write_text("x")
        b = tmp_path / "b.mp4"; b.write_text("x")

        session.load_playlist([PlaylistItem(a), PlaylistItem(b)])

        assert session.index == 0
        assert session.current_video == a
        assert player.opened[-1] == a

    def test_load_playlist_empty_is_noop(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        before = session.current_video
        session.load_playlist([])
        assert session.current_video == before


class TestVolume:
    def test_volume_defaults_to_full(self, tmp_path):
        session, _player, _tcode = _make_session(tmp_path)
        assert session.volume == MAX_VOLUME

    def test_set_volume_drives_the_player(self, tmp_path):
        session, player, _tcode = _make_session(tmp_path)

        session.set_volume(40)

        assert session.volume == 40
        assert player.volumes == [40]

    def test_set_volume_clamps_to_the_supported_range(self, tmp_path):
        session, _player, _tcode = _make_session(tmp_path)

        session.set_volume(400)
        assert session.volume == MAX_VOLUME

        session.set_volume(-10)
        assert session.volume == MIN_VOLUME


class TestSpeed:
    def test_speed_defaults_to_normal(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        assert session.speed == 1.0

    def test_set_speed_drives_player_and_resets_tcode(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        resets_before = tcode.resets

        session.set_speed(1.5)

        assert session.speed == 1.5
        assert player.speeds[-1] == 1.5
        assert tcode.resets == resets_before + 1  # re-time the in-flight move

    def test_set_speed_clamps_to_supported_range(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)

        session.set_speed(99.0)
        assert session.speed == MAX_RATE

        session.set_speed(0.001)
        assert session.speed == MIN_RATE

    def test_adjust_speed_steps_relative_and_clamps(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)

        session.adjust_speed(0.5)
        assert session.speed == 1.5

        session.adjust_speed(-1.0)
        assert session.speed == 0.5

    def test_set_speed_same_value_is_noop(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        resets_before = tcode.resets

        session.set_speed(1.0)  # already normal

        assert player.speeds == []  # no redundant player call
        assert tcode.resets == resets_before

    def test_advance_passes_current_speed_to_tcode(self, tmp_path):
        session, player, tcode = _make_session(tmp_path)
        session.set_speed(2.0)
        player.position_ms = 1500

        session.advance()

        assert tcode.updates[-1] == (1500, session.current_funscript, 2.0)


class TestTakingTheDeviceBack:
    """Every path that jumps the playback clock, or takes the device from
    whoever had it, resets the T-Code driver -- so the next waypoint glides
    from wherever the device really is instead of slamming across its range.

    Three of the paths that do it had nothing watching them, which is how a
    rule restated at eight call sites goes wrong: the one that forgets is
    invisible.
    """

    def test_leaving_a_loop_takes_it_back(self, tmp_path):
        """The playhead is about to carry on past the out point it was being
        held inside, which the device knows nothing about."""
        session, player, tcode = _make_session(tmp_path)
        player.position_ms = 2500
        session.record_down()
        player.position_ms = 3500
        session.record_up()
        resets_before = tcode.resets

        session.loop_cancel()

        assert tcode.resets == resets_before + 1

    def test_a_locked_video_wrapping_to_its_own_start_takes_it_back(self, tmp_path):
        """A seek to the start in all but name.  Without it, a clip scripted to
        its edges aims an un-glided waypoint from the last action's height to
        the first action's -- a full-range slam every pass."""
        session, player, tcode = _make_session(tmp_path)
        player.position_ms = 9_000
        session.advance()
        resets_before = tcode.resets

        player.position_ms = 20.0
        session.advance()

        assert tcode.resets == resets_before + 1

    def test_a_clock_that_only_ticks_forward_takes_nothing_over(self, tmp_path):
        """The control probe for all three rewind branches: an ordinary tick
        must not reset, or "reset on a rewind" would be indistinguishable from
        "reset every tick"."""
        session, player, tcode = _make_session(tmp_path)
        player.position_ms = 1_000
        session.advance()
        resets_before = tcode.resets

        player.position_ms = 1_100
        session.advance()

        assert tcode.resets == resets_before

    def test_opening_another_video_takes_it_back(self, tmp_path):
        """The new video's script starts from wherever it starts, and the
        device is still standing where the last one left it."""
        session, _player, tcode = _make_session(tmp_path, entries=2)
        resets_before = tcode.resets

        session.step(1)

        assert tcode.resets == resets_before + 1


HOUR_MS = 3_600_000.0


def test_the_session_writes_down_where_a_video_was_left(tmp_path):
    points = PlayPoints(tmp_path / "points.json")
    session, player, _ = _make_session(tmp_path, duration_ms=HOUR_MS, play_points=points)

    _watch_to(session, player, 300_000)

    assert points.point_for(session.current_video) == 300_000


def test_the_clock_left_over_from_the_last_video_is_not_written_down(tmp_path):
    points = PlayPoints(tmp_path / "points.json")
    session, player, _ = _make_session(tmp_path, entries=2, duration_ms=HOUR_MS,
                                       play_points=points)
    _watch_to(session, player, 300_000)

    session.step(1)
    player.position_ms = 300_000  # mpv still reporting the video just left
    session.advance()

    assert points.point_for(session.current_video) == 0


def test_a_video_left_in_the_middle_opens_there_again(tmp_path):
    points = PlayPoints(tmp_path / "points.json")
    session, player, _ = _make_session(tmp_path, entries=2, duration_ms=HOUR_MS,
                                       play_points=points)
    _watch_to(session, player, 300_000)

    session.step(1)
    session.step(-1)

    assert player.seeks[-1] == 300_000


def test_a_video_is_only_resumed_once_its_file_is_open(tmp_path):
    points = PlayPoints(tmp_path / "points.json")
    for _tick in range(2):
        points.observe(tmp_path / "v0.mp4", position_ms=300_000, duration_ms=HOUR_MS)
    session, player, _ = _make_session(tmp_path, duration_ms=0.0, play_points=points)
    assert player.seeks == []

    player.duration_ms = HOUR_MS
    session.advance()

    assert player.seeks == [300_000]


def test_a_loop_handed_back_beats_the_point_the_video_was_left_at(tmp_path):
    points = PlayPoints(tmp_path / "points.json")
    for _tick in range(2):
        points.observe(tmp_path / "v0.mp4", position_ms=300_000, duration_ms=HOUR_MS)
    session, player, _ = _make_session(tmp_path, duration_ms=0.0, play_points=points)

    player.duration_ms = HOUR_MS
    session.restore_loop(10_000, 20_000)
    session.advance()

    assert player.seeks[-1] == 10_000


def test_leaving_a_video_for_another_writes_down_the_very_spot(tmp_path):
    file = tmp_path / "points.json"
    session, player, _ = _make_session(tmp_path, entries=2, duration_ms=HOUR_MS,
                                       play_points=PlayPoints(file))
    _watch_to(session, player, 300_000)
    player.position_ms = 300_048
    session.advance()

    session.step(1)

    assert PlayPoints(file).point_for(tmp_path / "v0.mp4") == 300_048


def test_closing_the_player_writes_down_the_very_spot(tmp_path):
    file = tmp_path / "points.json"
    session, player, _ = _make_session(tmp_path, duration_ms=HOUR_MS,
                                       play_points=PlayPoints(file))
    _watch_to(session, player, 300_000)
    player.position_ms = 300_048
    session.advance()

    session.close()

    assert PlayPoints(file).point_for(tmp_path / "v0.mp4") == 300_048


def test_a_video_that_played_itself_out_is_not_remembered_at_its_end(tmp_path):
    file = tmp_path / "points.json"
    session, player, _ = _make_session(tmp_path, entries=2, duration_ms=HOUR_MS,
                                       play_points=PlayPoints(file))
    session.set_locked(False)
    _watch_to(session, player, HOUR_MS - 40)

    player.eof = True
    session.advance()

    assert player.opened[-1] == tmp_path / "v1.mp4"
    assert PlayPoints(file).point_for(tmp_path / "v0.mp4") == 0
