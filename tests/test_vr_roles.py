from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from player_core.modes import LoopState
from player_core.playback_rate import MAX_RATE, MIN_RATE

from fun_time.event_log import NOTICE, SOURCE_MAIN
from fun_time.player_status import read_main_player_status
from fun_time_vr.layout import TILT_LIMIT_DEG
from fun_time_vr.projection import EQUIRECT_180_SBS, FISHEYE_190_SBS, FLAT
from fun_time_vr.roles import TILT_STEP_DEG, MainRole
from main_player.play_points import PlayPoints
from tests.mpv_refusals import RefusesSeeks


def _never_quits() -> None:
    """The quit hook, for the verbs that must not reach it."""
    raise AssertionError("QUIT was not the command under test")


class FakePlayer(RefusesSeeks):
    """The _MpvControl surface, recorded — mirrors tests/satellite_fakes.py's idea."""

    def __init__(self):
        self.loaded: list[Path] = []
        self.paused: bool | None = None
        self.speed = 1.0
        self.volume: int | None = None
        self.muted: bool | None = None
        self.seeks: list[float] = []
        self.position_ms = 0.0
        self.duration_ms = 60_000.0
        self.closed = False
        # The main player opens locked on either display, which is mpv's own
        # loop_file — the option the real one is constructed with.
        self.loop_file = True
        self.ab_loop: tuple[float, float] | None = None
        self.eof = False
        self.paces: list[float] = []
        self.showing_picture = False
        self.pushes = 0

    def set_pace(self, seconds: float) -> None:
        self.paces.append(seconds)

    def push_still(self) -> None:
        self.pushes += 1

    def load(self, path: Path) -> None:
        self.loaded.append(Path(path))
        # Opening a file is what clears mpv's end-of-file flag; the role's
        # step-at-eof latch is written against that.
        self.eof = False

    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    def set_speed(self, speed: float) -> None:
        self.speed = speed

    def set_volume(self, volume: int) -> None:
        self.volume = volume

    def set_muted(self, muted: bool) -> None:
        self.muted = muted

    def set_loop_file(self, loop: bool) -> None:
        self.loop_file = loop

    def set_ab_loop(self, in_ms: float, out_ms: float) -> None:
        self.ab_loop = (in_ms, out_ms)

    def clear_ab_loop(self) -> None:
        self.ab_loop = None

    def seek_ms(self, ms: float) -> None:
        self.refuse_if_asked()
        self.seeks.append(ms)
        self.position_ms = ms

    def close(self) -> None:
        self.closed = True


class FakeDriver:
    def __init__(self):
        self.updates: list[tuple[int, float]] = []
        self.scripts: list = []
        self.parks = 0
        self.resets = 0
        self.closed = False

    def update(self, position_ms, fs, *, now=None, speed=1.0):
        self.updates.append((position_ms, speed))
        self.scripts.append(fs)

    def park(self, *, now=None):
        self.parks += 1

    def reset(self):
        self.resets += 1

    def close(self):
        self.closed = True


def _write_funscript(path: Path) -> None:
    path.write_text(json.dumps({"actions": [
        {"at": 0, "pos": 0}, {"at": 400, "pos": 100}, {"at": 800, "pos": 0},
    ]}), encoding="utf-8")


@pytest.fixture
def role_parts(tmp_path):
    videos = tmp_path / "videos" / "videos"
    metadata = tmp_path / "videos" / "metadata"
    vr_dir = videos / "VR" / "finished"
    flat_dir = videos / "2D" / "non_AI"
    vr_dir.mkdir(parents=True)
    flat_dir.mkdir(parents=True)
    metadata.mkdir(parents=True)

    one = vr_dir / "scene one.mp4"
    two = flat_dir / "scene two.mp4"
    three = vr_dir / "scene three.mp4"
    for video in (one, two, three):
        video.write_bytes(b"")
    script = tmp_path / "scene one.funscript"
    _write_funscript(script)

    playlist = tmp_path / "main_player_playlist.tsv"
    playlist.write_text(f"{one}\t{script}\n{two}\n{three}\n", encoding="utf-8")

    player, driver = FakePlayer(), FakeDriver()
    points = PlayPoints(tmp_path / "play_points.json")
    role = MainRole(
        player=player,
        driver=driver,
        playlist_file=playlist,
        metadata_root=metadata,
        vr_dirs=(vr_dir,),
        play_points=points,
    )
    return SimpleNamespace(
        role=role, player=player, driver=driver, playlist=playlist,
        metadata=metadata, files=(one, two, three, script), points=points,
    )


class TestAStillsMove:
    """A still does not simply sit there while it holds the headset's screen:
    every turn of the pump asks the player to carry its move a little further,
    the way the desktop players' loops do."""

    def test_every_turn_of_the_pump_carries_it_on(self, role_parts):
        role_parts.role.tick(now=0.0)

        assert role_parts.player.pushes == 1

    def test_a_frozen_room_asks_for_it_too(self, role_parts):
        """Frozen, the move holds where it had got to rather than stopping being
        asked for -- the hold is the player's to keep, and a tick that returned
        before asking would leave the picture wherever the last frame left it."""
        role_parts.role.set_paused(True)

        role_parts.role.tick(now=0.0)

        assert role_parts.player.pushes == 1


class TestPlaybackVerbs:
    def test_opens_on_the_first_entry_with_its_funscript_and_projection(self, role_parts):
        role, player, files = role_parts.role, role_parts.player, role_parts.files
        one, *_ = files
        assert player.loaded == [one]
        assert role.has_funscript is True
        assert role.projection == EQUIRECT_180_SBS

    def test_next_wraps_and_reresolves_funscript_and_projection(self, role_parts):
        role, player, files = role_parts.role, role_parts.player, role_parts.files
        one, two, three, script = files

        role.apply_command("NEXT", on_quit=_never_quits)
        assert player.loaded[-1] == two
        assert role.has_funscript is False
        assert role.projection == FLAT

        role.apply_command("NEXT", on_quit=_never_quits)
        role.apply_command("NEXT", on_quit=_never_quits)
        assert player.loaded[-1] == one  # wrapped

    def test_prev_steps_back(self, role_parts):
        role, player, files = role_parts.role, role_parts.player, role_parts.files
        one, two, three, script = files
        role.apply_command("PREV", on_quit=_never_quits)
        assert player.loaded[-1] == three

    def test_seek_verbs_step_ten_seconds(self, role_parts):
        role, player = role_parts.role, role_parts.player
        player.position_ms = 15_000
        role.apply_command("SEEK_FWD", on_quit=_never_quits)
        assert player.seeks[-1] == 25_000
        role.apply_command("SEEK_BACK", on_quit=_never_quits)
        assert player.seeks[-1] == 15_000

    def test_speed_verbs_step_and_clamp(self, role_parts):
        role, player = role_parts.role, role_parts.player
        role.apply_command("SPEED_UP", on_quit=_never_quits)
        assert player.speed == 1.25
        for _ in range(10):
            role.apply_command("SPEED_UP", on_quit=_never_quits)
        assert player.speed == MAX_RATE
        for _ in range(20):
            role.apply_command("SPEED_DOWN", on_quit=_never_quits)
        assert player.speed == MIN_RATE

    def test_set_speed_takes_min_max_and_numbers(self, role_parts):
        role, player = role_parts.role, role_parts.player
        role.apply_command("SET_SPEED max", on_quit=_never_quits)
        assert player.speed == MAX_RATE
        role.apply_command("SET_SPEED min", on_quit=_never_quits)
        assert player.speed == MIN_RATE
        role.apply_command("SET_SPEED 1.5", on_quit=_never_quits)
        assert player.speed == 1.5

    def test_set_pace_is_how_long_a_picture_holds_the_headset_screen(self, role_parts):
        role, player = role_parts.role, role_parts.player

        assert role.apply_command("SET_PACE 2.5", on_quit=_never_quits) is True
        assert player.paces == [2.5]

    def test_the_status_says_when_the_headset_screen_shows_a_picture(self, role_parts):
        role, player = role_parts.role, role_parts.player

        player.showing_picture = True

        assert role.status_fields(None)["picture"] == "1"

    def test_set_volume_carries_level_and_mute_once_audio_is_live(self, role_parts):
        role, player = role_parts.role, role_parts.player
        role.sound_goes_live()
        role.apply_command("SET_VOLUME 40 1", on_quit=_never_quits)
        assert player.volume == 40
        assert player.muted is True
        role.apply_command("SET_VOLUME 70 0", on_quit=_never_quits)
        assert player.muted is False

    def test_set_volume_before_audio_is_live_records_without_unsilencing(self, role_parts):
        """In VR the main player starts silent and the host un-silences it once the
        headset is presenting; a SET_VOLUME arriving during that warm-up must
        record the level, not blare it out of the desktop speakers."""
        role, player = role_parts.role, role_parts.player
        role.apply_command("SET_VOLUME 70 0", on_quit=_never_quits)

        assert role.volume == 70
        assert role.muted is False
        assert player.muted is None  # never touched


    def test_the_recorded_level_comes_on_when_the_sound_does(self, role_parts):
        """The host used to set the flag and then re-apply the level itself, so
        the role owned the value and the host owned the moment it took effect."""
        role, player = role_parts.role, role_parts.player
        role.apply_command("SET_VOLUME 70 1", on_quit=_never_quits)

        role.sound_goes_live()

        assert player.volume == 70
        assert player.muted is True

    def test_play_file_jumps_to_a_playlist_item(self, role_parts):
        role, player, files = role_parts.role, role_parts.player, role_parts.files
        one, two, three, script = files
        role.apply_command(f"PLAY_FILE {two}", on_quit=_never_quits)
        assert player.loaded[-1] == two
        role.apply_command("NEXT", on_quit=_never_quits)
        assert player.loaded[-1] == three  # resumed from two's slot, not spliced anew

    def test_play_file_splices_a_newcomer_with_its_funscript(self, role_parts, tmp_path):
        role, player = role_parts.role, role_parts.player
        newcomer = tmp_path / "videos" / "videos" / "VR" / "finished" / "scene four.mp4"
        newcomer.write_bytes(b"")
        script = tmp_path / "scene four.funscript"
        _write_funscript(script)

        role.apply_command(f"PLAY_FILE {newcomer}\t{script}", on_quit=_never_quits)

        assert player.loaded[-1] == newcomer
        assert role.has_funscript is True

    def test_reload_playlist_keeps_the_playing_video_when_it_survives(self, role_parts):
        role, player, playlist, files = (
            role_parts.role, role_parts.player, role_parts.playlist, role_parts.files)
        one, two, three, script = files
        playlist.write_text(f"{three}\n{one}\t{script}\n", encoding="utf-8")

        role.apply_command("RELOAD_PLAYLIST", on_quit=_never_quits)

        assert player.loaded == [one]  # never reloaded — still playing
        role.apply_command("NEXT", on_quit=_never_quits)
        assert player.loaded[-1] == three  # wrapped within the new list

    def test_reload_playlist_restarts_at_the_top_when_current_is_gone(self, role_parts):
        role, player, playlist, files = (
            role_parts.role, role_parts.player, role_parts.playlist, role_parts.files)
        one, two, three, script = files
        playlist.write_text(f"{two}\n{three}\n", encoding="utf-8")

        role.apply_command("RELOAD_PLAYLIST", on_quit=_never_quits)

        assert player.loaded[-1] == two

    def test_quit_sets_the_stop_flag(self, role_parts):
        role = role_parts.role
        fired = []
        role.apply_command("QUIT", on_quit=lambda: fired.append(True))
        assert fired == [True]

    def test_unknown_verb_reports_unhandled(self, role_parts):
        """One the role really does not answer, named in
        `UNIMPLEMENTED_MAIN_PLAYER_VERBS` and held to that by
        `test_vr_control_parity.py`."""
        role = role_parts.role
        assert role.apply_command("CYCLE_VERSION", on_quit=_never_quits) is False


class TestTheProjectionAPictureIsWrappedIn:
    """The headset keeps a video's last picture up while the next one opens, and
    that picture goes on being wrapped the way its own video is."""

    def test_the_video_before_keeps_its_projection_once_the_next_is_up(self, role_parts):
        role, (one, two, *_) = role_parts.role, role_parts.files

        role.apply_command("NEXT", on_quit=_never_quits)

        assert role.projection_of(str(one)) == EQUIRECT_180_SBS
        assert role.projection_of(str(two)) == FLAT


class TestProjectionCycling:
    def test_cycle_advances_and_persists_to_the_sidecar(self, role_parts):
        role, metadata, files = role_parts.role, role_parts.metadata, role_parts.files
        one, *_ = files

        role.apply_command("CYCLE_PROJECTION", on_quit=_never_quits)

        assert role.projection == FISHEYE_190_SBS
        sidecar = metadata / "VR" / "finished" / "scene one.json"
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        assert payload["vr"]["projection"] == "fisheye_190_sbs"

    def test_the_persisted_choice_holds_when_the_video_comes_back(self, role_parts):
        role = role_parts.role
        role.apply_command("CYCLE_PROJECTION", on_quit=_never_quits)
        role.apply_command("NEXT", on_quit=_never_quits)
        role.apply_command("PREV", on_quit=_never_quits)
        assert role.projection == FISHEYE_190_SBS


class TestRecenter:
    def test_recenter_is_carried_until_the_host_takes_it(self, role_parts):
        role = role_parts.role
        assert role.recenter.take() is False
        assert role.apply_command("RECENTER", on_quit=_never_quits) is True
        assert role.recenter.take() is True
        # Consumed: the host applies one re-zero per request, not per frame.
        assert role.recenter.take() is False

    def test_repeated_requests_collapse_into_one(self, role_parts):
        role = role_parts.role
        role.apply_command("RECENTER", on_quit=_never_quits)
        role.apply_command("RECENTER", on_quit=_never_quits)
        assert role.recenter.take() is True
        assert role.recenter.take() is False


class TestLayoutReset:
    def test_a_layout_reset_is_carried_until_the_host_takes_it(self, role_parts):
        role = role_parts.role
        assert role.layout_reset.take() is False
        assert role.apply_command("LAYOUT_RESET", on_quit=_never_quits) is True
        assert role.layout_reset.take() is True
        assert role.layout_reset.take() is False

    def test_putting_the_screens_back_levels_them_too(self, role_parts):
        """One arrangement: the spots and the tilt go back together."""
        role = role_parts.role
        role.apply_command("TILT_DOWN", on_quit=_never_quits)
        assert role.tilt_deg != 0.0

        assert role.apply_command("LAYOUT_RESET", on_quit=_never_quits) is True

        assert role.tilt_deg == 0.0
        assert role.layout_reset.take() is True


class TestTilt:
    def _opened_at(self, tmp_path, tilt_deg):
        playlist = tmp_path / "main_player_playlist.tsv"
        playlist.write_text(f"{tmp_path / 'feature.mp4'}\t\n", encoding="utf-8")
        return MainRole(player=FakePlayer(), driver=FakeDriver(), playlist_file=playlist,
                        metadata_root=None, vr_dirs=(), tilt_deg=tilt_deg)

    def test_the_room_opens_at_the_tilt_the_last_session_was_left_at(self, tmp_path):
        assert self._opened_at(tmp_path, -20.0).tilt_deg == pytest.approx(-20.0)

    def test_a_tilt_it_is_opened_at_is_held_to_the_travel(self, tmp_path):
        assert self._opened_at(tmp_path, 400.0).tilt_deg == pytest.approx(TILT_LIMIT_DEG)

    def test_the_verbs_walk_the_tilt_up_and_down_in_steps(self, role_parts):
        role = role_parts.role
        assert role.tilt_deg == 0.0
        assert role.apply_command("TILT_UP", on_quit=_never_quits) is True
        assert role.tilt_deg == pytest.approx(TILT_STEP_DEG)
        role.apply_command("TILT_DOWN", on_quit=_never_quits)
        role.apply_command("TILT_DOWN", on_quit=_never_quits)
        assert role.tilt_deg == pytest.approx(-TILT_STEP_DEG)

    def test_the_travel_stops_at_straight_up_and_straight_down(self, role_parts):
        role = role_parts.role
        for _ in range(int(TILT_LIMIT_DEG / TILT_STEP_DEG) + 20):
            role.apply_command("TILT_UP", on_quit=_never_quits)
        assert role.tilt_deg == pytest.approx(TILT_LIMIT_DEG)
        for _ in range(int(2 * TILT_LIMIT_DEG / TILT_STEP_DEG) + 20):
            role.apply_command("TILT_DOWN", on_quit=_never_quits)
        assert role.tilt_deg == pytest.approx(-TILT_LIMIT_DEG)

    def test_the_controller_shares_the_one_angle_and_its_clamp(self, role_parts):
        role = role_parts.role
        role.apply_command("TILT_UP", on_quit=_never_quits)
        role.nudge_tilt(0.4)
        assert role.tilt_deg == pytest.approx(TILT_STEP_DEG + 0.4)
        role.nudge_tilt(1000.0)
        assert role.tilt_deg == pytest.approx(TILT_LIMIT_DEG)

    def test_reset_puts_the_screens_back_level(self, role_parts):
        role = role_parts.role
        role.apply_command("TILT_DOWN", on_quit=_never_quits)
        assert role.apply_command("TILT_RESET", on_quit=_never_quits) is True
        assert role.tilt_deg == 0.0

    def test_a_recenter_leaves_the_tilt_alone(self, role_parts):
        # Turning to face another way says nothing about how the viewer is
        # lying, so re-zeroing the heading must not stand the screens up.
        role = role_parts.role
        role.apply_command("TILT_DOWN", on_quit=_never_quits)
        tilted = role.tilt_deg
        role.apply_command("RECENTER", on_quit=_never_quits)
        role.recenter.take()
        assert role.tilt_deg == pytest.approx(tilted)


class TestTCode:
    def test_scripted_video_drives_waypoints_at_the_current_speed(self, role_parts):
        role, player, driver = role_parts.role, role_parts.player, role_parts.driver
        player.position_ms = 5_000
        role.apply_command("SET_SPEED 1.5", on_quit=_never_quits)

        role.tick(now=1.0)

        assert driver.updates == [(5_000, 1.5)]

    def test_unscripted_video_parks(self, role_parts):
        role, driver = role_parts.role, role_parts.driver
        role.apply_command("NEXT", on_quit=_never_quits)  # scene two: no funscript

        role.tick(now=1.0)

        assert driver.parks == 1
        assert driver.updates == []

    def test_disabled_tcode_sends_nothing(self, role_parts):
        role, driver = role_parts.role, role_parts.driver
        role.apply_command("SET_TCODE_ENABLED 0", on_quit=_never_quits)

        role.tick(now=1.0)

        assert driver.updates == []
        assert driver.parks == 0

    def test_paused_sends_nothing(self, role_parts):
        role, driver = role_parts.role, role_parts.driver
        role.set_paused(True)

        role.tick(now=1.0)

        assert driver.updates == []
        assert driver.parks == 0

    def test_unpausing_holds_the_device_until_the_picture_moves(self, role_parts):
        """Un-pausing is a request, not a picture: mpv takes frames to start
        presenting, and the device led it by that much every time a session was
        revealed -- which is what he felt as the OSR2 starting before the video."""
        role, driver, player = role_parts.role, role_parts.driver, role_parts.player
        role.set_paused(True)
        role.set_paused(False)

        role.tick(now=1.0)
        assert driver.updates == []
        assert driver.parks == 0

        player.position_ms += 40
        role.tick(now=1.1)

        assert driver.updates or driver.parks

    def test_unpausing_a_still_picture_drives_the_device_at_once(self, role_parts):
        role, driver, player = role_parts.role, role_parts.driver, role_parts.player
        role.apply_command("NEXT", on_quit=_never_quits)  # scene two: no funscript
        player.showing_picture = True
        role.set_paused(True)
        role.set_paused(False)

        role.tick(now=1.0)

        assert driver.parks == 1

    def test_a_picture_that_has_moved_once_is_not_asked_again(self, role_parts):
        """The gate is the resume's own edge, not a per-tick liveness check: a
        video legitimately still between frames must not stop the device."""
        role, driver, player = role_parts.role, role_parts.driver, role_parts.player
        role.set_paused(True)
        role.set_paused(False)
        player.position_ms += 40
        role.tick(now=1.0)
        drove = len(driver.updates) + driver.parks

        role.tick(now=1.1)  # the same position, one tick later

        assert len(driver.updates) + driver.parks > drove

    def test_navigation_resets_the_driver_edge_gate(self, role_parts):
        role, driver = role_parts.role, role_parts.driver
        resets_at_start = driver.resets
        role.apply_command("NEXT", on_quit=_never_quits)
        assert driver.resets == resets_at_start + 1

    def test_re_enabling_tcode_resets_the_driver_for_the_takeover(self, role_parts):
        # SET_TCODE_ENABLED 1 is the video-mode handoff taking the device back from
        # Genau: reset like any other takeover, so the next tick sends at once
        # and with the handoff glide rather than snapping to a near waypoint.
        role, driver = role_parts.role, role_parts.driver
        role.apply_command("SET_TCODE_ENABLED 0", on_quit=_never_quits)
        resets_before = driver.resets

        role.apply_command("SET_TCODE_ENABLED 1", on_quit=_never_quits)

        assert driver.resets == resets_before + 1

    def test_repeating_enabled_tcode_does_not_reset(self, role_parts):
        # Only the mute→drive edge is a takeover; repeating "1" must not keep
        # re-arming the glide under a script that is already driving.
        role, driver = role_parts.role, role_parts.driver
        resets_before = driver.resets

        role.apply_command("SET_TCODE_ENABLED 1", on_quit=_never_quits)

        assert driver.resets == resets_before


class TestTheMainSlotLock:
    """One padlock for whichever player owns the main slot, and in video mode
    that is this one: the apostrophe, the console's padlock and the spoken
    "main lock" all arrive here as the main player's own three verbs."""

    def test_a_fresh_role_is_locked_the_way_the_player_opens(self, role_parts):
        """On is the main player's opening state on either display — the
        ``loop_file=True`` the VR player is constructed with — so the console's
        padlock is right before the first status is even published."""
        assert role_parts.role.locked is True
        assert role_parts.player.loop_file is True

    def test_toggle_hands_the_end_of_the_file_back_to_the_playlist(self, role_parts):
        role, player = role_parts.role, role_parts.player
        role.apply_command("TOGGLE_LOCK", on_quit=_never_quits)
        assert (role.locked, player.loop_file) == (False, False)
        role.apply_command("TOGGLE_LOCK", on_quit=_never_quits)
        assert (role.locked, player.loop_file) == (True, True)

    def test_the_absolute_pair_asks_for_the_state_it_names(self, role_parts):
        """The spoken forms are "main lock" and "main unlock": a speaker asks
        for the state they want, so saying it twice must not undo it."""
        role = role_parts.role
        for _ in range(2):
            role.apply_command("LOCK_OFF", on_quit=_never_quits)
            assert role.locked is False
        for _ in range(2):
            role.apply_command("LOCK_ON", on_quit=_never_quits)
            assert role.locked is True

    def test_the_padlock_goes_out_in_the_status_file(self, role_parts, tmp_path):
        """The console that draws it is not always this player — in genau mode
        it is drawn over Genau's clip — so the flag travels with the status."""
        role = role_parts.role
        status_file = tmp_path / "main_player_status.txt"

        def published() -> bool:
            text = "".join(f"{k}={v}\n" for k, v in role.status_fields(None).items())
            status_file.write_text(text, encoding="utf-8")
            return read_main_player_status(status_file).locked

        assert published() is True
        role.apply_command("LOCK_OFF", on_quit=_never_quits)
        assert published() is False

    def test_an_unlocked_video_ending_steps_to_the_next(self, role_parts):
        role, player, files = role_parts.role, role_parts.player, role_parts.files
        _one, two, *_ = files
        role.apply_command("LOCK_OFF", on_quit=_never_quits)
        player.eof = True

        role.tick(now=0.0)

        assert player.loaded[-1] == two

    def test_a_locked_video_ending_stays_where_it_is(self, role_parts):
        """Locked is mpv's own loop-file, which restarts the file rather than
        ending it — so this is belt and braces, and it is what keeps a lock
        applied at the very end of a file from stepping off it."""
        role, player = role_parts.role, role_parts.player
        player.eof = True

        role.tick(now=0.0)

        assert len(player.loaded) == 1

    def test_the_step_is_taken_once_however_long_mpv_keeps_saying_end_of_file(
        self, role_parts,
    ):
        """``loadfile`` is asynchronous: mpv goes on reporting end-of-file for a
        tick or two after the step is issued, and reading that again would walk
        past a whole video before the new one had opened."""
        role, player, files = role_parts.role, role_parts.player, role_parts.files
        _one, two, *_ = files
        role.apply_command("LOCK_OFF", on_quit=_never_quits)
        player.eof = True

        for _ in range(5):
            role.tick(now=0.0)
            player.eof = True  # the fake's load() clears it; mpv would not, yet

        assert player.loaded[-1] == two
        assert len(player.loaded) == 2

    def test_a_paused_player_never_walks_its_own_playlist(self, role_parts):
        """Which is what makes OmniPause a settled state: freeze the flag and
        nothing moves on by itself."""
        role, player = role_parts.role, role_parts.player
        role.apply_command("LOCK_OFF", on_quit=_never_quits)
        role.set_paused(True)
        player.eof = True

        role.tick(now=0.0)

        assert len(player.loaded) == 1

    def test_locking_spends_an_end_of_file_the_unlock_would_have_stepped_on(
        self, role_parts,
    ):
        """A video sitting at end-of-file when the lock goes on has already
        spent it; without clearing the latch the next unlock stepped off at
        once, from a video that had been repeating happily for minutes."""
        role, player = role_parts.role, role_parts.player
        role.apply_command("LOCK_OFF", on_quit=_never_quits)
        player.eof = True
        role.tick(now=0.0)  # steps, and latches
        role.apply_command("LOCK_ON", on_quit=_never_quits)

        assert role._stepped_at_eof is False


class TestFMode:
    """Fun Time's F-mode over this player's playlist.  The list arrives already
    narrowed and a scripted playlist looks like any other, so the flag has to be
    said outright — and the panel's status line is the only thing that says it."""

    def test_a_fresh_role_is_not_in_it(self, role_parts):
        assert role_parts.role.scripted_filter is False

    def test_the_flag_is_taken_from_the_verb(self, role_parts):
        role = role_parts.role
        role.apply_command("SET_F_MODE 1", on_quit=_never_quits)
        assert role.scripted_filter is True
        role.apply_command("SET_F_MODE 0", on_quit=_never_quits)
        assert role.scripted_filter is False


class TestTheLoop:
    """The headset marks and runs an A/B loop inside one video, as the desktop
    main player does: `main_player.loop_machine` is the machine both drive, and
    what it does with a range is `test_main_player_loop_machine.py`.  These are
    about the role -- the clock each verb reads, and the tick that watches it."""

    def _on_an_unscripted_video(self, role_parts):
        """The second entry, so a marked range is the raw one rather than the
        one the first video's funscript snaps it out to."""
        role_parts.role.apply_command("NEXT", on_quit=_never_quits)
        return role_parts.role

    def test_pressing_and_letting_go_marks_the_loop_the_playhead_was_at(self, role_parts):
        role, player = self._on_an_unscripted_video(role_parts), role_parts.player

        player.position_ms = 20_000.0
        role.apply_command("RECORD_DOWN", on_quit=_never_quits)
        assert role.loop_state is LoopState.RECORDING

        player.position_ms = 25_000.0
        role.apply_command("RECORD_UP", on_quit=_never_quits)

        assert role.loop_state is LoopState.LOOPING
        assert player.ab_loop == (20_000, 25_000), "mpv loops the range natively"
        assert player.seeks[-1] == 20_000, "the playhead lands on the loop's start"

    def test_a_tap_walks_the_three_states_for_a_speaker_or_a_button(self, role_parts):
        role, player = self._on_an_unscripted_video(role_parts), role_parts.player

        player.position_ms = 20_000.0
        role.apply_command("RECORD_TAP", on_quit=_never_quits)
        assert role.loop_state is LoopState.RECORDING

        player.position_ms = 25_000.0
        role.apply_command("RECORD_TAP", on_quit=_never_quits)
        assert (role.loop_state, player.ab_loop) == (LoopState.LOOPING, (20_000, 25_000))

        role.apply_command("RECORD_TAP", on_quit=_never_quits)
        assert (role.loop_state, player.ab_loop) == (LoopState.NORMAL, None)

    def test_pressing_record_again_on_a_running_loop_drops_it(self, role_parts):
        """R is held to mark and released to loop, and pressing it again is how
        the loop is left -- so RECORD_DOWN on a running loop cancels, in the
        headset as on the desktop."""
        role, player = self._on_an_unscripted_video(role_parts), role_parts.player
        player.position_ms = 20_000.0
        role.apply_command("RECORD_DOWN", on_quit=_never_quits)
        player.position_ms = 25_000.0
        role.apply_command("RECORD_UP", on_quit=_never_quits)

        role.apply_command("RECORD_DOWN", on_quit=_never_quits)

        assert (role.loop_state, player.ab_loop) == (LoopState.NORMAL, None)

    def test_dropping_the_loop_takes_the_device_back_over(self, role_parts):
        """The playhead is about to carry on past the out point it was being held
        inside, which the OSR2 knows nothing about."""
        role, player = self._on_an_unscripted_video(role_parts), role_parts.player
        player.position_ms = 20_000.0
        role.apply_command("RECORD_DOWN", on_quit=_never_quits)
        player.position_ms = 25_000.0
        role.apply_command("RECORD_UP", on_quit=_never_quits)
        resets_before = role_parts.driver.resets

        role.apply_command("LOOP_CANCEL", on_quit=_never_quits)

        assert (role.loop_state, player.ab_loop) == (LoopState.NORMAL, None)
        assert role_parts.driver.resets > resets_before

    def test_a_loop_the_last_session_was_left_running_is_put_back(self, role_parts):
        """The orchestrator reads the bounds off the status file and sends them
        over the video the resume put at the top of the playlist; they are
        finished bounds, so no gesture is replayed and nothing is snapped again."""
        role, player = role_parts.role, role_parts.player

        assert role.apply_command("SET_LOOP 2000 4000", on_quit=_never_quits) is True

        assert (role.loop_state, role.loop_bounds) == (LoopState.LOOPING, (2000, 4000))
        assert player.ab_loop == (2000, 4000)
        assert player.seeks[-1] == 2000

    def test_a_restored_loop_waits_for_a_file_that_is_still_opening(self, role_parts):
        """A crossing queues this before mpv has the file, and mpv reports no
        duration for a tick or two -- a seek taken then would be clamped against
        a zero-length video and land back at the top."""
        role, player = role_parts.role, role_parts.player
        player.duration_ms = 0.0
        player.seeks.clear()

        role.apply_command("SET_LOOP 2000 4000", on_quit=_never_quits)

        assert player.ab_loop == (2000, 4000), "mpv takes the range whenever it is set"
        assert player.seeks == []

        player.duration_ms = 60_000.0
        role.tick(now=0.0)

        assert player.seeks[-1] == 2000

    def test_an_empty_range_is_no_loop_to_put_back(self, role_parts):
        """What the status file says when nothing is looping."""
        role = role_parts.role

        role.apply_command("SET_LOOP 0 0", on_quit=_never_quits)

        assert role.loop_state is LoopState.NORMAL
        assert role_parts.player.ab_loop is None

    def test_a_set_loop_it_cannot_read_as_two_numbers_is_refused(self, role_parts):
        role = role_parts.role

        for value in ("", "2000", "2000 later"):
            assert role.apply_command(f"SET_LOOP {value}", on_quit=_never_quits) is False

        assert role.loop_state is LoopState.NORMAL

    def test_the_next_video_comes_up_with_no_loop(self, role_parts):
        role, player = role_parts.role, role_parts.player
        role.apply_command("SET_LOOP 2000 4000", on_quit=_never_quits)

        role.apply_command("NEXT", on_quit=_never_quits)

        assert role.loop_state is LoopState.NORMAL
        assert player.ab_loop is None


class TestTheLoopAcrossATick:
    """What the pump makes of a loop: the two things no gesture can say."""

    def _marking_near_the_end(self, role_parts):
        role, player = role_parts.role, role_parts.player
        role.apply_command("NEXT", on_quit=_never_quits)  # unscripted, so no snapping
        player.duration_ms = 30_000.0
        player.position_ms = 28_000.0
        role.apply_command("RECORD_DOWN", on_quit=_never_quits)
        return role, player

    def test_a_mark_that_runs_to_the_end_of_the_file_closes_and_starts_there(self, role_parts):
        """Before loop-file wraps the whole video back to the start, which would
        flash its opening frames."""
        role, player = self._marking_near_the_end(role_parts)

        player.position_ms = 29_950.0
        role.tick(now=0.0)

        assert role.loop_state is LoopState.LOOPING
        assert player.ab_loop == (28_000, 29_950)
        assert player.seeks[-1] == 28_000

    def test_a_running_loops_wrap_takes_the_device_back_over(self, role_parts):
        """mpv wraps the A/B range B->A by rewinding the clock, and the OSR2 was
        told nothing about it."""
        role, player = role_parts.role, role_parts.player
        role.apply_command("SET_LOOP 20000 25000", on_quit=_never_quits)
        player.position_ms = 24_900.0
        role.tick(now=0.0)
        resets_before = role_parts.driver.resets

        player.position_ms = 20_000.0
        role.tick(now=0.0)

        assert role_parts.driver.resets == resets_before + 1

    def test_the_end_of_the_file_does_not_step_off_a_running_loop(self, role_parts):
        """The A/B range owns the end of the video while a loop is running."""
        role, player = role_parts.role, role_parts.player
        role.apply_command("LOCK_OFF", on_quit=_never_quits)
        role.apply_command("SET_LOOP 20000 25000", on_quit=_never_quits)
        loaded_before = len(player.loaded)
        player.eof = True

        role.tick(now=0.0)

        assert len(player.loaded) == loaded_before, "still on the video it was looping"

    def test_the_end_of_the_file_does_not_step_off_a_mark_in_progress(self, role_parts):
        role, player = self._marking_near_the_end(role_parts)
        role.apply_command("LOCK_OFF", on_quit=_never_quits)
        loaded_before = len(player.loaded)
        player.eof = True

        role.tick(now=0.0)

        assert len(player.loaded) == loaded_before


class TestStatus:
    def test_status_fields_read_back_through_the_orchestrators_own_parser(self, role_parts, tmp_path):
        role, player = role_parts.role, role_parts.player
        player.position_ms = 1_000.0
        status_file = tmp_path / "main_player_status.txt"

        text = "".join(f"{k}={v}\n" for k, v in role.status_fields(None).items())
        status_file.write_text(text, encoding="utf-8")
        status = read_main_player_status(status_file)

        assert status.video.endswith("scene one.mp4")
        assert status.has_funscript is True
        assert status.paused is False
        assert status.loop_state is LoopState.NORMAL
        assert status.loop_bounds is None
        # position 1s sits inside the fabricated script's dense cluster
        assert status.funscript_resting is False
        assert status.funscript_driving is True

    def test_resting_is_reported_in_a_quiet_stretch(self, role_parts):
        role, player = role_parts.role, role_parts.player
        player.position_ms = 40_000.0  # far past the last action at 800ms
        fields = role.status_fields(None)
        assert fields["funscript_resting"] == "1"

    def test_the_touch_the_panel_chose_is_published_for_the_arbiter(self, role_parts, tmp_path):
        """Where the console panel drew Genau's turn ending, so the arbiter ends
        it there and not at a trough of its own choosing."""
        status_file = tmp_path / "main_player_status.txt"
        fields = role_parts.role.status_fields(3_600)
        status_file.write_text("".join(f"{k}={v}\n" for k, v in fields.items()), encoding="utf-8")

        assert read_main_player_status(status_file).handoff_touch_ms == 3_600

    def test_the_loop_it_is_running_is_published_for_the_next_session(self, role_parts,
                                                                     tmp_path):
        """A loop is a range inside one video and lives nowhere but in the player
        holding it, so a session that never published it could never be handed it
        back on the way out of the headset."""
        role = role_parts.role
        role.apply_command("SET_LOOP 2000 4000", on_quit=_never_quits)
        status_file = tmp_path / "main_player_status.txt"

        fields = role.status_fields(None)
        status_file.write_text("".join(f"{k}={v}\n" for k, v in fields.items()),
                               encoding="utf-8")
        status = read_main_player_status(status_file)

        assert status.loop_state is LoopState.LOOPING
        assert status.loop_bounds == (2000, 4000)

    def test_a_mark_still_open_publishes_no_bounds(self, role_parts):
        """A range nothing is looping yet would hand the next session a loop it
        cannot play."""
        role, player = role_parts.role, role_parts.player
        player.position_ms = 300.0
        role.apply_command("RECORD_DOWN", on_quit=_never_quits)

        fields = role.status_fields(None)

        assert fields["loop_state"] == "recording"
        assert (fields["loop_in_ms"], fields["loop_out_ms"]) == ("0", "0")

    def test_no_touch_publishes_an_empty_field_rather_than_a_zero(self, role_parts):
        assert role_parts.role.status_fields(None)["handoff_touch_ms"] == ""

    def test_the_rate_the_video_plays_at_is_published_for_the_satellites_to_take(self, role_parts):
        role = role_parts.role

        role.apply_command("SPEED_UP", on_quit=_never_quits)

        assert role.status_fields(None)["speed"] == "1.25"


class TestTheScriptAsItPlays:
    def test_a_locked_video_plays_its_script_again_each_time_round(self, role_parts):
        assert role_parts.role.funscript_as_played.position_at(60_400) == 100

    def test_an_unlocked_video_plays_its_script_once(self, role_parts):
        role = role_parts.role

        role.set_locked(False)

        assert role.funscript_as_played is role.current_funscript

    def test_a_running_loop_plays_its_stretch_again_and_again(self, role_parts):
        """The OSR2 keeps to the stretch on screen: without this it drove straight
        past the loop's out point and on through the rest of the video's script."""
        role = role_parts.role
        role.set_locked(False)

        role.apply_command("SET_LOOP 400 800", on_quit=_never_quits)

        assert role.funscript_as_played.position_at(1_200) == 100

    def test_the_device_is_driven_as_it_plays(self, role_parts):
        role, player, driver = role_parts.role, role_parts.player, role_parts.driver
        player.position_ms = 5_000

        role.tick(now=1.0)

        assert driver.scripts[-1] is role.funscript_as_played

    def test_keeps_a_gap_too_short_to_hand_over_before_it_comes_round(self, role_parts):
        role, player = role_parts.role, role_parts.player
        player.duration_ms = 10_000.0
        player.position_ms = 8_000.0

        assert role.current_funscript.is_resting_at(8_000) is True
        assert role.status_fields(None)["funscript_resting"] == "0"


class TestWhatTheDriveGateReadsOffIt:
    """The panel's drive gate reads the role as the main player's reads its session: the
    script in play and the rate the video runs at."""

    def test_the_script_in_play(self, role_parts):
        role = role_parts.role

        assert role.funscript_as_played is not None
        role.apply_command("NEXT", on_quit=_never_quits)
        assert role.funscript_as_played is None

    def test_the_rate_the_video_runs_at(self, role_parts):
        role = role_parts.role

        role.apply_command("SPEED_UP", on_quit=_never_quits)

        assert role.speed == 1.25


class TestWhetherItIsTheDisplay:
    """DISPLAY_ON / DISPLAY_OFF ride every mode switch: the mirror of the HUD
    verb Genau's role gets, so exactly one of the two claims the scene."""

    def _role(self, tmp_path):
        playlist = tmp_path / "main_player_playlist.tsv"
        playlist.write_text(f"{tmp_path / 'feature.mp4'}\t\n", encoding="utf-8")
        return MainRole(player=FakePlayer(), driver=FakeDriver(), playlist_file=playlist,
                        metadata_root=None, vr_dirs=())

    def test_a_fresh_role_is_the_display(self, tmp_path):
        assert self._role(tmp_path).displayed is True

    def test_display_off_steps_it_out_of_the_scene(self, tmp_path):
        role = self._role(tmp_path)

        assert role.apply_command("DISPLAY_OFF", on_quit=_never_quits) is True
        assert role.displayed is False

    def test_display_on_puts_it_back(self, tmp_path):
        role = self._role(tmp_path)
        role.apply_command("DISPLAY_OFF", on_quit=_never_quits)

        role.apply_command("DISPLAY_ON", on_quit=_never_quits)

        assert role.displayed is True


class TestScenes:
    def _on_a_video_with_scenes_at(self, role_parts, *starts_s):
        sidecar = role_parts.metadata / "2D" / "non_AI" / "scene two.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            json.dumps({"scenes": [{"start": start} for start in starts_s]}), encoding="utf-8")
        role_parts.role.apply_command("NEXT", on_quit=_never_quits)
        return role_parts.role, role_parts.player

    @staticmethod
    def _told(caplog) -> list[tuple[int, str]]:
        return [(record.levelno, record.source) for record in caplog.records
                if hasattr(record, "source")]

    def test_next_scene_seeks_to_where_the_next_one_begins(self, role_parts):
        role, player = self._on_a_video_with_scenes_at(role_parts, 0, 120, 300)
        player.position_ms = 45_000.0

        assert role.apply_command("NEXT_SCENE", on_quit=_never_quits) is True
        assert player.seeks[-1] == 120_000.0

    def test_next_scene_from_where_a_jump_just_landed_goes_on_to_the_one_after(self, role_parts):
        role, player = self._on_a_video_with_scenes_at(role_parts, 0, 120, 300)
        player.position_ms = 119_960.0

        role.apply_command("NEXT_SCENE", on_quit=_never_quits)

        assert player.seeks[-1] == 300_000.0

    def test_previous_scene_goes_back_to_where_this_one_began(self, role_parts):
        role, player = self._on_a_video_with_scenes_at(role_parts, 0, 120, 300)
        player.position_ms = 200_000.0

        assert role.apply_command("PREV_SCENE", on_quit=_never_quits) is True
        assert player.seeks[-1] == 120_000.0

    def test_previous_scene_in_the_first_moments_of_one_goes_to_the_one_before(self, role_parts):
        role, player = self._on_a_video_with_scenes_at(role_parts, 0, 120, 300)
        player.position_ms = 121_000.0

        role.apply_command("PREV_SCENE", on_quit=_never_quits)

        assert player.seeks[-1] == 0.0

    def test_a_video_with_no_scenes_marked_says_so_on_its_picture(self, role_parts, caplog):
        role, player = role_parts.role, role_parts.player
        player.position_ms = 45_000.0

        with caplog.at_level(logging.DEBUG, logger="fun_time_vr.roles"):
            role.apply_command("NEXT_SCENE", on_quit=_never_quits)
            role.apply_command("PREV_SCENE", on_quit=_never_quits)

        assert self._told(caplog) == [(NOTICE, SOURCE_MAIN)] * 2
        assert player.seeks == []

    def test_no_scene_left_to_go_to_is_said_on_its_picture_too(self, role_parts, caplog):
        role, player = self._on_a_video_with_scenes_at(role_parts, 0, 120, 300)

        with caplog.at_level(logging.DEBUG, logger="fun_time_vr.roles"):
            player.position_ms = 400_000.0
            role.apply_command("NEXT_SCENE", on_quit=_never_quits)
            player.position_ms = 1_000.0
            role.apply_command("PREV_SCENE", on_quit=_never_quits)

        assert self._told(caplog) == [(NOTICE, SOURCE_MAIN)] * 2
        assert player.seeks == []


class TestSeekTo:
    def test_the_panels_scrubber_seeks_the_video_to_a_time(self, role_parts):
        role_parts.role.seek_to(12_345.0)

        assert role_parts.player.seeks[-1] == 12_345.0

    def test_a_seek_is_held_within_the_video(self, role_parts):
        role_parts.role.seek_to(-5.0)
        role_parts.role.seek_to(role_parts.player.duration_ms + 5.0)

        assert role_parts.player.seeks[-2:] == [0.0, role_parts.player.duration_ms]


class TestReopen:
    """The way out of a wedged pipeline: the main player is the one mpv here
    that keeps an audio track, and mpv's clock follows audio, so an output
    device that stops draining freezes its video on one frame."""

    def test_it_loads_the_same_video_again_and_seeks_back(self, role_parts):
        role_parts.player.position_ms = 8_000.0

        role_parts.role.reopen()

        assert role_parts.player.loaded[-1] == role_parts.role.current_video
        assert role_parts.player.seeks[-1] == 8_000.0

    def test_a_seek_back_mpv_will_not_take_yet_is_asked_for_again(self, role_parts):
        role_parts.player.position_ms = 8_000.0
        role_parts.player.refuse_seeks(1)

        role_parts.role.reopen()
        role_parts.role.tick(now=2.0)

        assert role_parts.player.seeks[-1] == 8_000.0

    def test_it_does_not_seek_a_video_that_never_started(self, role_parts):
        """Frozen on frame one is the shape this is for; seeking to zero would
        only ask mpv for a seek it does not need."""
        role_parts.player.position_ms = 0.0

        role_parts.role.reopen()

        assert role_parts.player.seeks == []

    def test_it_keeps_the_role_paused_if_it_was(self, role_parts):
        role_parts.role.set_paused(True)

        role_parts.role.reopen()

        assert role_parts.player.paused is True
        assert role_parts.role.paused is True


class TestWhatTheHeadsetCallsTheVideo:
    """The console panel's muted line names what is playing, and a download's
    filename is the worst name the library has for it."""

    def _role(self, tmp_path, payload: dict):
        videos = tmp_path / "videos" / "videos" / "2D" / "non_AI"
        metadata = tmp_path / "videos" / "metadata"
        videos.mkdir(parents=True)
        metadata.mkdir(parents=True)
        video = videos / "Jane Doe - Alpha Study Part Two_apo8_iris2.mp4"
        video.write_bytes(b"")
        sidecar = (metadata / video.relative_to(tmp_path / "videos" / "videos")
                   ).with_suffix(".json")
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(json.dumps(payload), encoding="utf-8")
        playlist = tmp_path / "main_player_playlist.tsv"
        playlist.write_text(f"{video}\n", encoding="utf-8")
        return MainRole(player=FakePlayer(), driver=FakeDriver(), playlist_file=playlist,
                        metadata_root=metadata, vr_dirs=())

    def test_a_clip_is_named_by_the_pair_recorded_for_it(self, tmp_path):
        role = self._role(tmp_path, {
            "clip": {"performer": "Jane Doe", "source": "Alpha Study: Part Two"},
        })

        assert role.title == "Jane Doe - Alpha Study: Part Two"

    def test_a_scene_wears_the_name_evolver_recorded_for_it(self, tmp_path):
        """Which is the name of the clip cut out of it -- worked out once over
        the whole library, so the headset reads it off the one sidecar in front
        of it rather than indexing a library it does not hold."""
        role = self._role(tmp_path, {"video": {"type": "full_length"},
                                     "title": "Jane Doe - Alpha Study 3"})

        assert role.title == "Jane Doe - Alpha Study 3"

    def test_a_video_with_no_record_keeps_its_filename(self, tmp_path):
        role = self._role(tmp_path, {"video": {"type": "full_length"}})

        assert role.title == "Jane Doe - Alpha Study Part Two_apo8_iris2"


HOUR_MS = 3_600_000.0


def _watch(role, player, position_ms):
    """Two ticks at *position_ms*: the jump onto it, then playing on from it."""
    player.position_ms = position_ms
    role.tick(now=1.0)
    role.tick(now=1.1)


class TestWhereAVideoWasLeft:
    def test_a_video_left_in_the_middle_opens_there_again(self, role_parts):
        role, player = role_parts.role, role_parts.player
        player.duration_ms = HOUR_MS
        _watch(role, player, 300_000)

        role.apply_command("NEXT", on_quit=_never_quits)
        role.apply_command("PREV", on_quit=_never_quits)
        role.tick(now=2.0)

        assert player.seeks[-1] == 300_000

    def test_a_video_is_only_resumed_once_its_file_is_open(self, role_parts):
        role, player = role_parts.role, role_parts.player
        player.duration_ms = HOUR_MS
        _watch(role, player, 300_000)
        role.apply_command("NEXT", on_quit=_never_quits)
        role.apply_command("PREV", on_quit=_never_quits)
        player.duration_ms = 0.0
        seeks = len(player.seeks)

        role.tick(now=2.0)
        assert len(player.seeks) == seeks

        player.duration_ms = HOUR_MS
        role.tick(now=2.1)

        assert player.seeks[-1] == 300_000

    def test_a_spot_mpv_will_not_take_yet_is_asked_for_again(self, role_parts):
        role, player = role_parts.role, role_parts.player
        player.duration_ms = HOUR_MS
        _watch(role, player, 300_000)
        role.apply_command("NEXT", on_quit=_never_quits)
        role.apply_command("PREV", on_quit=_never_quits)
        player.refuse_seeks(1)

        role.tick(now=2.0)
        role.tick(now=2.1)

        assert player.seeks[-1] == 300_000

    def test_leaving_a_video_for_another_writes_down_the_very_spot(self, role_parts):
        role, player, points = role_parts.role, role_parts.player, role_parts.points
        one, *_ = role_parts.files
        player.duration_ms = HOUR_MS
        _watch(role, player, 300_000)
        player.position_ms = 300_048
        role.tick(now=1.2)

        role.apply_command("NEXT", on_quit=_never_quits)

        assert points.point_for(one) == 300_048

    def test_closing_the_player_writes_down_the_very_spot(self, role_parts):
        role, player, points = role_parts.role, role_parts.player, role_parts.points
        one, *_ = role_parts.files
        player.duration_ms = HOUR_MS
        _watch(role, player, 300_000)
        player.position_ms = 300_048
        role.tick(now=1.2)

        role.close()

        assert points.point_for(one) == 300_048

    def test_a_video_that_played_itself_out_is_not_remembered_at_its_end(self, role_parts):
        role, player, points = role_parts.role, role_parts.player, role_parts.points
        one, two, *_ = role_parts.files
        player.duration_ms = HOUR_MS
        role.set_locked(False)
        _watch(role, player, HOUR_MS - 40)

        player.eof = True
        role.tick(now=2.0)

        assert player.loaded[-1] == two
        assert points.point_for(one) == 0
