from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fun_time.player_status import read_nau_status
from fun_time_vr.projection import EQUIRECT_180_SBS, FISHEYE_190_SBS, FLAT
from fun_time_vr.roles import MAX_SPEED, MIN_SPEED, TILT_LIMIT_DEG, TILT_STEP_DEG, MainRole


def _never_quits() -> None:
    """The quit hook, for the verbs that must not reach it."""
    raise AssertionError("QUIT was not the command under test")


class FakePlayer:
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
        self.eof = False

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

    def seek_ms(self, ms: float) -> None:
        self.seeks.append(ms)
        self.position_ms = ms

    def close(self) -> None:
        self.closed = True


class FakeDriver:
    def __init__(self):
        self.updates: list[tuple[int, float]] = []
        self.parks = 0
        self.resets = 0
        self.closed = False

    def update(self, position_ms, fs, *, now=None, speed=1.0):
        self.updates.append((position_ms, speed))

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

    playlist = tmp_path / "nau_playlist.tsv"
    playlist.write_text(f"{one}\t{script}\n{two}\n{three}\n", encoding="utf-8")

    player, driver = FakePlayer(), FakeDriver()
    role = MainRole(
        player=player,
        driver=driver,
        playlist_file=playlist,
        metadata_root=metadata,
        vr_dirs=(vr_dir,),
    )
    return SimpleNamespace(
        role=role, player=player, driver=driver, playlist=playlist,
        metadata=metadata, files=(one, two, three, script),
    )


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
        assert player.speed == MAX_SPEED
        for _ in range(20):
            role.apply_command("SPEED_DOWN", on_quit=_never_quits)
        assert player.speed == MIN_SPEED

    def test_set_speed_takes_min_max_and_numbers(self, role_parts):
        role, player = role_parts.role, role_parts.player
        role.apply_command("SET_SPEED max", on_quit=_never_quits)
        assert player.speed == MAX_SPEED
        role.apply_command("SET_SPEED min", on_quit=_never_quits)
        assert player.speed == MIN_SPEED
        role.apply_command("SET_SPEED 1.5", on_quit=_never_quits)
        assert player.speed == 1.5

    def test_set_volume_carries_level_and_mute_once_audio_is_live(self, role_parts):
        role, player = role_parts.role, role_parts.player
        role.audio_live = True
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
        role = role_parts.role
        assert role.apply_command("RECORD_DOWN", on_quit=_never_quits) is False


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
        assert role.take_recenter() is False
        assert role.apply_command("RECENTER", on_quit=_never_quits) is True
        assert role.take_recenter() is True
        # Consumed: the host applies one re-zero per request, not per frame.
        assert role.take_recenter() is False

    def test_repeated_requests_collapse_into_one(self, role_parts):
        role = role_parts.role
        role.apply_command("RECENTER", on_quit=_never_quits)
        role.apply_command("RECENTER", on_quit=_never_quits)
        assert role.take_recenter() is True
        assert role.take_recenter() is False


class TestTilt:
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
        role.take_recenter()
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
    "main lock" all arrive here as Nau's own three verbs."""

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
        status_file = tmp_path / "nau_status.txt"

        def published() -> bool:
            text = "".join(f"{k}={v}\n" for k, v in role.status_fields(None).items())
            status_file.write_text(text, encoding="utf-8")
            return read_nau_status(status_file).locked

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
        assert role_parts.role.f_mode is False

    def test_the_flag_is_taken_from_the_verb(self, role_parts):
        role = role_parts.role
        role.apply_command("SET_F_MODE 1", on_quit=_never_quits)
        assert role.f_mode is True
        role.apply_command("SET_F_MODE 0", on_quit=_never_quits)
        assert role.f_mode is False


class TestStatus:
    def test_status_fields_read_back_through_the_orchestrators_own_parser(self, role_parts, tmp_path):
        role, player = role_parts.role, role_parts.player
        player.position_ms = 1_000.0
        status_file = tmp_path / "nau_status.txt"

        text = "".join(f"{k}={v}\n" for k, v in role.status_fields(None).items())
        status_file.write_text(text, encoding="utf-8")
        status = read_nau_status(status_file)

        assert status.video.endswith("scene one.mp4")
        assert status.has_funscript is True
        assert status.paused is False
        assert status.state == "normal"
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
        status_file = tmp_path / "nau_status.txt"
        fields = role_parts.role.status_fields(3_600)
        status_file.write_text("".join(f"{k}={v}\n" for k, v in fields.items()), encoding="utf-8")

        assert read_nau_status(status_file).handoff_touch_ms == 3_600

    def test_no_touch_publishes_an_empty_field_rather_than_a_zero(self, role_parts):
        assert role_parts.role.status_fields(None)["handoff_touch_ms"] == ""


class TestWhatTheDriveGateReadsOffIt:
    """The panel's drive gate reads the role as Nau's reads its session: the
    script in play and the rate the video runs at."""

    def test_the_script_in_play(self, role_parts):
        role = role_parts.role

        assert role.current_funscript is not None
        role.apply_command("NEXT", on_quit=_never_quits)
        assert role.current_funscript is None

    def test_the_rate_the_video_runs_at(self, role_parts):
        role = role_parts.role

        role.apply_command("SPEED_UP", on_quit=_never_quits)

        assert role.speed == 1.25


class TestWhetherItIsTheDisplay:
    """DISPLAY_ON / DISPLAY_OFF ride every mode switch: the mirror of the HUD
    verb Genau's role gets, so exactly one of the two claims the scene."""

    def _role(self, tmp_path):
        playlist = tmp_path / "nau_playlist.tsv"
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
