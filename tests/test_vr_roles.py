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

    def load(self, path: Path) -> None:
        self.loaded.append(Path(path))

    def set_paused(self, paused: bool) -> None:
        self.paused = paused

    def set_speed(self, speed: float) -> None:
        self.speed = speed

    def set_volume(self, volume: int) -> None:
        self.volume = volume

    def set_muted(self, muted: bool) -> None:
        self.muted = muted

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

    def test_play_file_jumps_to_a_playlist_member(self, role_parts):
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
        # SET_TCODE_ENABLED 1 is the hybrid handoff taking the device back from
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


class TestStatus:
    def test_status_fields_read_back_through_the_orchestrators_own_parser(self, role_parts, tmp_path):
        role, player = role_parts.role, role_parts.player
        player.position_ms = 1_000.0
        status_file = tmp_path / "nau_status.txt"

        text = "".join(f"{k}={v}\n" for k, v in role.status_fields().items())
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
        fields = role.status_fields()
        assert fields["funscript_resting"] == "1"
