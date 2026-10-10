"""The headset's own verbs on the Main Funestra: a projection to walk and a
field of view to dial, written into the video's record; a heading to re-zero
onto, a tilt and a layout to put back; a scene to jump to."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from player_core.playback import Playback

from fun_time.event_log import NOTICE, SOURCE_MAIN
from fun_time_vr.headset_verbs import ANGLE_STEP, DIAL_STILL_S, TILT_STEP_DEG, HeadsetVerbs
from fun_time_vr.layout import TILT_LIMIT_DEG
from fun_time_vr.picture_shape import FISHEYE_CIRCLE
from fun_time_vr.projection import EQUIRECT_180_SBS, FISHEYE_180_SBS, FLAT, ProjectionMemory
from tests.satellite_fakes import FakeSatellitePlayer

HOUR_MS = 3_600_000.0


class _ALook:
    """PictureLook's surface, with the looking done by hand: ``found`` is the
    call the real one makes from its own thread once it has seen a shape twice."""

    def __init__(self, looked: list[Path]) -> None:
        self._looked = looked
        self._asked: tuple[Path, object] | None = None
        self.ended = False

    def look_at(self, video: Path, on_shape) -> None:
        self._looked.append(video)
        self._asked = (video, on_shape)

    def found(self, shape: str, *, video: Path | None = None) -> None:
        looked_at, on_shape = self._asked
        on_shape(video or looked_at, shape)

    def stop_looking(self) -> None:
        self._asked = None
        self.ended = True


@pytest.fixture
def headset(tmp_path):
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
    player = FakeSatellitePlayer(duration_ms=HOUR_MS)
    playback = Playback([one, two, three], player=player, locked=True)
    verbs = HeadsetVerbs(playback, metadata_root=metadata, vr_dirs=(vr_dir,))
    return SimpleNamespace(verbs=verbs, playback=playback, player=player, metadata=metadata,
                           files=(one, two, three))


def _step(headset, delta: int = 1) -> None:
    """The item moves on the Funestra's playback; the headset's verbs notice
    on their next pass."""
    headset.playback.step(delta)
    headset.verbs.tick(now=0.0)


def test_a_verb_that_is_not_the_headsets_own_is_refused(headset):
    assert headset.verbs.apply("NEXT") is False
    assert headset.verbs.apply("QUIT") is False


class TestTheProjectionAPictureIsWrappedIn:
    """The headset keeps a video's last picture up while the next one opens, and
    that picture goes on being wrapped the way its own video is."""

    def test_it_opens_on_the_first_item_in_its_projection(self, headset):
        assert headset.verbs.projection == EQUIRECT_180_SBS

    def test_the_video_before_keeps_its_projection_once_the_next_is_up(self, headset):
        one, two, _three = headset.files

        _step(headset)

        assert headset.verbs.projection_of(str(one)) == EQUIRECT_180_SBS
        assert headset.verbs.projection_of(str(two)) == FLAT
        assert headset.verbs.projection == FLAT


class TestLookingAtWhatIsOnScreen:
    """A VR video nobody has chosen a projection for gets its picture looked at,
    and a fisheye circle found there is what it opens in from then on."""

    def _looking(self, headset):
        looked: list[Path] = []
        headset.verbs.look_with(_ALook(looked))
        return looked

    def test_the_video_already_on_screen_is_looked_at_as_soon_as_there_is_a_look(self, headset):
        assert self._looking(headset) == [headset.files[0]]

    def test_each_vr_video_opened_after_that_is_looked_at_too(self, headset):
        looked = self._looking(headset)

        _step(headset, -1)  # scene three, a VR video

        assert looked[-1] == headset.files[2]

    def test_a_flat_video_is_not(self, headset):
        looked = self._looking(headset)
        del looked[:]

        _step(headset)  # scene two, a flat video

        assert looked == []

    def test_a_video_that_needs_no_look_stops_the_look_the_last_one_was_getting(self, headset):
        look = _ALook([])
        headset.verbs.look_with(look)
        assert not look.ended

        _step(headset)  # scene two, a flat video

        assert look.ended

    def test_a_circle_found_in_the_picture_is_what_it_opens_in_from_then_on(self, headset):
        look = _ALook([])
        headset.verbs.look_with(look)
        _step(headset, -1)
        assert headset.verbs.projection == EQUIRECT_180_SBS

        look.found(FISHEYE_CIRCLE)

        assert headset.verbs.projection == FISHEYE_180_SBS
        _step(headset)
        _step(headset, -1)
        assert headset.verbs.projection == FISHEYE_180_SBS
        assert headset.playback.current_video == headset.files[2]

    def test_a_shape_found_for_a_video_no_longer_on_screen_changes_nothing(self, headset):
        look = _ALook([])
        headset.verbs.look_with(look)
        _step(headset, -1)
        looked_at = headset.playback.current_video
        _step(headset)  # back to scene one

        look.found(FISHEYE_CIRCLE, video=looked_at)

        assert headset.verbs.projection == EQUIRECT_180_SBS

    def test_the_look_is_ended_with_the_session(self, headset):
        look = _ALook([])
        headset.verbs.look_with(look)

        headset.verbs.close()

        assert look.ended

    def test_a_session_with_nothing_looking_still_opens_its_videos(self, headset):
        _step(headset, -1)

        assert headset.verbs.projection == EQUIRECT_180_SBS


class TestProjectionCycling:
    def test_cycle_advances_and_persists_to_the_sidecar(self, headset):
        assert headset.verbs.apply("CYCLE_PROJECTION") is True

        assert headset.verbs.projection == FISHEYE_180_SBS
        sidecar = headset.metadata / "VR" / "finished" / "scene one.json"
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        assert payload["vr"]["projection"] == "fisheye_180_sbs"

    def test_the_persisted_choice_holds_when_the_video_comes_back(self, headset):
        headset.verbs.apply("CYCLE_PROJECTION")
        _step(headset)
        _step(headset, -1)
        assert headset.verbs.projection == FISHEYE_180_SBS

    def test_a_choice_holds_for_a_vr_folder_kept_away_from_the_rest_of_the_library(self, tmp_path):
        metadata = tmp_path / "local" / "videos" / "metadata"
        vr_dir = tmp_path / "cloud" / "videos" / "videos" / "VR" / "finished"
        vr_dir.mkdir(parents=True)
        one, two = vr_dir / "scene one.mp4", vr_dir / "scene two.mp4"
        playback = Playback([one, two], player=FakeSatellitePlayer(), locked=True)
        verbs = HeadsetVerbs(playback, metadata_root=metadata, vr_dirs=(vr_dir,))

        verbs.apply("CYCLE_PROJECTION")
        playback.step(1)
        verbs.tick(now=0.0)
        playback.step(-1)
        verbs.tick(now=0.0)

        assert verbs.projection == FISHEYE_180_SBS

    def test_cycling_back_steps_the_other_way_and_persists_too(self, headset):
        assert headset.verbs.apply("CYCLE_PROJECTION_BACK") is True

        assert headset.verbs.projection == FLAT
        sidecar = headset.metadata / "VR" / "finished" / "scene one.json"
        assert json.loads(sidecar.read_text(encoding="utf-8"))["vr"]["projection"] == "flat"


class TestDialingAWrappedPicture:
    def test_a_video_nobody_has_dialed_has_neither_a_field_of_view_nor_a_height(self, headset):
        one, *_ = headset.files
        assert headset.verbs.fov_of(str(one)) is None
        assert headset.verbs.height_of(str(one)) is None

    def test_what_is_dialed_belongs_to_the_video_playing_and_no_other(self, headset):
        one, two, *_ = headset.files

        headset.verbs.set_fov(143.0)
        headset.verbs.set_height(1.25)

        assert (headset.verbs.fov_of(str(one)), headset.verbs.height_of(str(one))) == (143.0, 1.25)
        assert (headset.verbs.fov_of(str(two)), headset.verbs.height_of(str(two))) == (None, None)

    def test_it_is_written_beside_the_projection_once_the_dial_has_been_still_for_a_moment(
            self, headset):
        headset.verbs.apply("CYCLE_PROJECTION")
        headset.verbs.set_fov(143.0)
        headset.verbs.set_height(1.25)

        headset.verbs.tick(now=0.0)
        headset.verbs.tick(now=DIAL_STILL_S + 0.1)

        sidecar = headset.metadata / "VR" / "finished" / "scene one.json"
        assert json.loads(sidecar.read_text(encoding="utf-8"))["vr"] == {
            "projection": "fisheye_180_sbs", "fov": 143.0, "height": 1.25}

    def test_it_is_not_written_while_it_keeps_changing(self, headset):
        headset.verbs.set_fov(140.0)
        headset.verbs.tick(now=0.0)
        headset.verbs.set_fov(141.0)
        headset.verbs.tick(now=DIAL_STILL_S * 0.9)
        headset.verbs.set_fov(142.0)
        headset.verbs.tick(now=DIAL_STILL_S * 1.8)

        assert not (headset.metadata / "VR" / "finished" / "scene one.json").exists()

    def test_it_is_written_and_said_once_however_long_it_then_stays_still(self, headset, caplog):
        headset.verbs.set_fov(143.0)
        headset.verbs.tick(now=0.0)

        with caplog.at_level(logging.INFO):
            for now in (DIAL_STILL_S + 0.1, DIAL_STILL_S * 3, DIAL_STILL_S * 5):
                headset.verbs.tick(now=now)

        assert caplog.text.count("field of view 143 degrees") == 1

    def test_a_video_nobody_dialed_is_never_written_or_talked_about(self, headset, caplog):
        with caplog.at_level(logging.INFO):
            for now in (0.0, DIAL_STILL_S * 2, DIAL_STILL_S * 4):
                headset.verbs.tick(now=now)

        assert not (headset.metadata / "VR" / "finished" / "scene one.json").exists()
        assert "field of view" not in caplog.text

    def test_a_dial_still_moving_when_the_next_video_opens_is_written_before_it_goes(
            self, headset):
        one, *_ = headset.files
        headset.verbs.set_fov(143.0)
        headset.verbs.tick(now=0.0)

        _step(headset)

        sidecar = headset.metadata / "VR" / "finished" / "scene one.json"
        assert json.loads(sidecar.read_text(encoding="utf-8"))["vr"]["fov"] == 143.0
        _step(headset, -1)
        assert headset.verbs.fov_of(str(one)) == 143.0

    def test_a_dial_the_pass_never_saw_is_written_before_the_video_goes_too(self, headset):
        headset.verbs.set_height(1.25)

        _step(headset)

        sidecar = headset.metadata / "VR" / "finished" / "scene one.json"
        assert json.loads(sidecar.read_text(encoding="utf-8"))["vr"]["height"] == 1.25

    def test_it_holds_when_the_video_comes_back(self, headset):
        one, *_ = headset.files
        headset.verbs.set_fov(143.0)
        headset.verbs.set_height(1.25)
        headset.verbs.tick(now=0.0)
        headset.verbs.tick(now=DIAL_STILL_S + 0.1)

        _step(headset)
        _step(headset, -1)

        assert (headset.verbs.fov_of(str(one)), headset.verbs.height_of(str(one))) == (143.0, 1.25)

    def test_a_video_opens_at_what_an_earlier_session_kept_for_it(self, headset):
        _, _, three = headset.files
        memory = ProjectionMemory(headset.metadata, (three.parent,))
        memory.save_fov(str(three), 151.0)
        memory.save_height(str(three), 0.8)

        _step(headset)
        _step(headset)

        assert (headset.verbs.fov_of(str(three)), headset.verbs.height_of(str(three))) == (151.0, 0.8)

    def test_what_a_video_opened_with_is_not_written_back_or_talked_about(self, headset, caplog):
        _, _, three = headset.files
        ProjectionMemory(headset.metadata, (three.parent,)).save_fov(str(three), 151.0)
        _step(headset)
        _step(headset)

        with caplog.at_level(logging.INFO):
            for now in (0.0, DIAL_STILL_S * 2, DIAL_STILL_S * 4):
                headset.verbs.tick(now=now)

        assert "field of view" not in caplog.text


class TestTheKeyboardWidensOrNarrowsTheAngle:
    def test_a_press_asks_the_host_for_a_slightly_wider_angle_once(self, headset):
        assert headset.verbs.apply("WIDEN_PROJECTION") is True

        assert headset.verbs.angle_asked.take() == pytest.approx(ANGLE_STEP)
        assert headset.verbs.angle_asked.take() == 1.0

    def test_a_press_asks_for_a_slightly_narrower_one(self, headset):
        assert headset.verbs.apply("NARROW_PROJECTION") is True

        assert headset.verbs.angle_asked.take() == pytest.approx(1 / ANGLE_STEP)

    def test_presses_before_the_host_looks_add_up(self, headset):
        for _ in range(3):
            headset.verbs.apply("WIDEN_PROJECTION")
        headset.verbs.apply("NARROW_PROJECTION")

        assert headset.verbs.angle_asked.take() == pytest.approx(ANGLE_STEP ** 2)


class TestRecenter:
    def test_recenter_is_carried_until_the_host_takes_it(self, headset):
        assert headset.verbs.recenter.take() is False
        assert headset.verbs.apply("RECENTER") is True
        assert headset.verbs.recenter.take() is True
        assert headset.verbs.recenter.take() is False

    def test_repeated_requests_collapse_into_one(self, headset):
        headset.verbs.apply("RECENTER")
        headset.verbs.apply("RECENTER")
        assert headset.verbs.recenter.take() is True
        assert headset.verbs.recenter.take() is False


class TestLayoutReset:
    def test_a_layout_reset_is_carried_until_the_host_takes_it(self, headset):
        assert headset.verbs.layout_reset.take() is False
        assert headset.verbs.apply("LAYOUT_RESET") is True
        assert headset.verbs.layout_reset.take() is True
        assert headset.verbs.layout_reset.take() is False

    def test_putting_the_screens_back_levels_them_too(self, headset):
        headset.verbs.apply("TILT_DOWN")
        assert headset.verbs.tilt_deg != 0.0

        assert headset.verbs.apply("LAYOUT_RESET") is True

        assert headset.verbs.tilt_deg == 0.0
        assert headset.verbs.layout_reset.take() is True


class TestTilt:
    def _opened_at(self, tmp_path, tilt_deg):
        feature = tmp_path / "feature.mp4"
        return HeadsetVerbs(Playback([feature], player=FakeSatellitePlayer(), locked=True),
                            metadata_root=None, vr_dirs=(), tilt_deg=tilt_deg)

    def test_the_room_opens_at_the_tilt_the_last_session_was_left_at(self, tmp_path):
        assert self._opened_at(tmp_path, -20.0).tilt_deg == pytest.approx(-20.0)

    def test_a_tilt_it_is_opened_at_is_held_to_the_travel(self, tmp_path):
        assert self._opened_at(tmp_path, 400.0).tilt_deg == pytest.approx(TILT_LIMIT_DEG)

    def test_the_verbs_walk_the_tilt_up_and_down_in_steps(self, headset):
        assert headset.verbs.tilt_deg == 0.0
        assert headset.verbs.apply("TILT_UP") is True
        assert headset.verbs.tilt_deg == pytest.approx(TILT_STEP_DEG)
        headset.verbs.apply("TILT_DOWN")
        headset.verbs.apply("TILT_DOWN")
        assert headset.verbs.tilt_deg == pytest.approx(-TILT_STEP_DEG)

    def test_the_travel_stops_at_straight_up_and_straight_down(self, headset):
        for _ in range(int(TILT_LIMIT_DEG / TILT_STEP_DEG) + 20):
            headset.verbs.apply("TILT_UP")
        assert headset.verbs.tilt_deg == pytest.approx(TILT_LIMIT_DEG)
        for _ in range(int(2 * TILT_LIMIT_DEG / TILT_STEP_DEG) + 20):
            headset.verbs.apply("TILT_DOWN")
        assert headset.verbs.tilt_deg == pytest.approx(-TILT_LIMIT_DEG)

    def test_the_controller_shares_the_one_angle_and_its_clamp(self, headset):
        headset.verbs.apply("TILT_UP")
        headset.verbs.nudge_tilt(0.4)
        assert headset.verbs.tilt_deg == pytest.approx(TILT_STEP_DEG + 0.4)
        headset.verbs.nudge_tilt(1000.0)
        assert headset.verbs.tilt_deg == pytest.approx(TILT_LIMIT_DEG)

    def test_reset_puts_the_screens_back_level(self, headset):
        headset.verbs.apply("TILT_DOWN")
        assert headset.verbs.apply("TILT_RESET") is True
        assert headset.verbs.tilt_deg == 0.0

    def test_a_recenter_leaves_the_tilt_alone(self, headset):
        headset.verbs.apply("TILT_DOWN")
        tilted = headset.verbs.tilt_deg
        headset.verbs.apply("RECENTER")
        headset.verbs.recenter.take()
        assert headset.verbs.tilt_deg == pytest.approx(tilted)


class TestScenes:
    def _on_a_video_with_scenes_at(self, headset, *starts_s):
        sidecar = headset.metadata / "2D" / "non_AI" / "scene two.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            json.dumps({"scenes": [{"start": start} for start in starts_s]}), encoding="utf-8")
        _step(headset)
        return headset.verbs, headset.player

    @staticmethod
    def _told(caplog) -> list[tuple[int, str]]:
        return [(record.levelno, record.source) for record in caplog.records
                if hasattr(record, "source")]

    def test_next_scene_seeks_to_where_the_next_one_begins(self, headset):
        verbs, player = self._on_a_video_with_scenes_at(headset, 0, 120, 300)
        player.position_ms = 45_000.0

        assert verbs.apply("NEXT_SCENE") is True
        assert player.seeks[-1] == 120_000.0

    def test_next_scene_from_where_a_jump_just_landed_goes_on_to_the_one_after(self, headset):
        verbs, player = self._on_a_video_with_scenes_at(headset, 0, 120, 300)
        player.position_ms = 119_960.0

        verbs.apply("NEXT_SCENE")

        assert player.seeks[-1] == 300_000.0

    def test_previous_scene_goes_back_to_where_this_one_began(self, headset):
        verbs, player = self._on_a_video_with_scenes_at(headset, 0, 120, 300)
        player.position_ms = 200_000.0

        assert verbs.apply("PREV_SCENE") is True
        assert player.seeks[-1] == 120_000.0

    def test_previous_scene_in_the_first_moments_of_one_goes_to_the_one_before(self, headset):
        verbs, player = self._on_a_video_with_scenes_at(headset, 0, 120, 300)
        player.position_ms = 121_000.0

        verbs.apply("PREV_SCENE")

        assert player.seeks[-1] == 0.0

    def test_a_video_with_no_scenes_marked_says_so_on_its_picture(self, headset, caplog):
        headset.player.position_ms = 45_000.0

        with caplog.at_level(logging.DEBUG, logger="fun_time_vr.headset_verbs"):
            headset.verbs.apply("NEXT_SCENE")
            headset.verbs.apply("PREV_SCENE")

        assert self._told(caplog) == [(NOTICE, SOURCE_MAIN)] * 2
        assert headset.player.seeks == []

    def test_no_scene_left_to_go_to_is_said_on_its_picture_too(self, headset, caplog):
        verbs, player = self._on_a_video_with_scenes_at(headset, 0, 120, 300)

        with caplog.at_level(logging.DEBUG, logger="fun_time_vr.headset_verbs"):
            player.position_ms = 400_000.0
            verbs.apply("NEXT_SCENE")
            player.position_ms = 1_000.0
            verbs.apply("PREV_SCENE")

        assert self._told(caplog) == [(NOTICE, SOURCE_MAIN)] * 2
        assert player.seeks == []

    def test_a_video_in_a_vr_folder_kept_away_from_the_library_reads_its_scenes_off_its_record(
            self, tmp_path):
        """Its scenes are read from the record its projection is kept in."""
        metadata = tmp_path / "local" / "videos" / "metadata"
        vr_dir = tmp_path / "cloud" / "videos" / "videos" / "VR" / "finished"
        vr_dir.mkdir(parents=True)
        video = vr_dir / "scene one.mp4"
        sidecar = metadata / "VR" / "finished" / "scene one.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(json.dumps({"scenes": [{"start": 90}]}), encoding="utf-8")
        player = FakeSatellitePlayer(duration_ms=HOUR_MS)
        verbs = HeadsetVerbs(Playback([video], player=player, locked=True),
                             metadata_root=metadata, vr_dirs=(vr_dir,))

        verbs.apply("NEXT_SCENE")

        assert player.seeks == [90_000.0]
