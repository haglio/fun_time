from __future__ import annotations

from funestra.host_controls import BarHud

from fun_time_vr.projection import (
    EQUIRECT_180_SBS,
    EQUIRECT_360,
    FISHEYE_180_SBS,
    FISHEYE_190_SBS,
    FLAT,
    PROJECTIONS,
)
from fun_time_vr.projection_controls import ANGLE_LABEL, ProjectionControls
from fun_time_vr.wrap_readout import label


class _Verbs:
    def __init__(self, projection: str = EQUIRECT_180_SBS, fov: float | None = 160.0) -> None:
        self.projection, self.fov = projection, fov
        self.picked: list[str] = []
        self.angles: list[float] = []

    def watch_in(self, projection: str) -> None:
        self.picked.append(projection)
        self.projection = projection

    def set_fov(self, degrees: float) -> None:
        self.angles.append(degrees)
        self.fov = degrees


class _Showing:
    def __init__(self) -> None:
        self.now = True

    def __call__(self) -> bool:
        return self.now


def _controls(projection: str = EQUIRECT_180_SBS, fov: float | None = 160.0):
    verbs, showing = _Verbs(projection, fov), _Showing()
    return ProjectionControls(verbs=lambda: verbs, showing=showing), verbs, showing


class TestTheProjectionList:
    def test_offers_every_projection_by_the_name_the_hud_gives_it(self):
        controls, _verbs, _showing = _controls()

        assert controls.hud().choices == tuple(label(projection) for projection in PROJECTIONS)

    def test_its_field_names_the_projection_the_video_is_shown_in(self):
        controls, _verbs, _showing = _controls(FISHEYE_180_SBS)

        assert controls.hud().chosen == "Fisheye"

    def test_its_field_names_a_projection_the_list_leaves_out(self):
        controls, _verbs, _showing = _controls(FISHEYE_190_SBS)

        assert controls.hud().chosen == "Fisheye 190"

    def test_a_projection_picked_off_it_is_the_one_the_video_is_shown_in(self):
        controls, verbs, _showing = _controls()

        controls.pick(PROJECTIONS.index(FISHEYE_180_SBS))

        assert verbs.picked == [FISHEYE_180_SBS]
        assert controls.hud().chosen == "Fisheye"


class TestTheAngleBar:
    def test_reads_the_angle_the_video_was_dialed_to_between_its_projections_ends(self):
        controls, _verbs, _showing = _controls(EQUIRECT_180_SBS, fov=160.0)

        assert controls.hud().bar == BarHud(ANGLE_LABEL, 160.0, 40.0, 180.0, unit="°")

    def test_an_angle_never_dialed_is_the_one_the_projection_is_mastered_at(self):
        controls, _verbs, _showing = _controls(FISHEYE_180_SBS, fov=None)

        assert controls.hud().bar == BarHud(ANGLE_LABEL, 180.0, 40.0, 240.0, unit="°")

    def test_a_screen_and_the_360_have_none(self):
        flat, _flat_verbs, _shown = _controls(FLAT, fov=None)
        all_round, _round_verbs, _also_shown = _controls(EQUIRECT_360, fov=None)

        assert flat.hud().bar is None
        assert all_round.hud().bar is None

    def test_setting_it_dials_the_videos_angle(self):
        controls, verbs, _showing = _controls()

        controls.set_bar(110.0)

        assert verbs.angles == [110.0]
        assert controls.hud().bar.value == 110.0


class TestNothingToShow:
    def test_while_genau_has_the_main_player(self):
        controls, _verbs, showing = _controls()

        showing.now = False

        assert controls.hud() is None

    def test_before_the_headsets_verbs_are_made(self):
        controls = ProjectionControls(verbs=lambda: None, showing=lambda: True)

        assert controls.hud() is None
