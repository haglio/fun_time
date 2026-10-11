from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from PIL import Image
from shared_ui.palette import BG_BUTTON, BG_PRIMARY, BLUE, TEXT_PRIMARY

from fun_time.dashboard_layout import Rect
from fun_time_vr.projection import (
    EQUIRECT_180_SBS,
    EQUIRECT_360,
    FISHEYE_180_SBS,
    FLAT,
    PROJECTIONS,
)
from fun_time_vr.projection_controls import (
    HEIGHT,
    Angle,
    ProjectionControls,
    ProjectionPointer,
    bar_at,
    field_at,
    stops_at,
)

BLOCK = Rect(10, 300, 360, HEIGHT)
PLACED = (BLOCK.x, BLOCK.y, BLOCK.width, BLOCK.height)
NARROWED = Angle(160.0, 40.0, 180.0)


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


def _pointer(projection: str = EQUIRECT_180_SBS, fov: float | None = 160.0):
    verbs, showing = _Verbs(projection, fov), _Showing()
    pointer = ProjectionPointer(verbs=lambda: verbs, showing=showing)
    pointer.drawing()
    return pointer, verbs, showing


def _middle(rect: Rect) -> tuple[int, int]:
    return rect.x + rect.width // 2, rect.y + rect.height // 2


class TestTheProjectionField:
    def test_a_press_on_it_opens_the_list(self):
        pointer, verbs, _showing = _pointer()

        assert pointer.press(*_middle(field_at(BLOCK)), rect=PLACED)
        assert pointer.drawing().list_open
        assert verbs.picked == []

    def test_picking_a_projection_off_the_list_shows_the_video_in_it_and_closes_it(self):
        pointer, verbs, _showing = _pointer()
        pointer.press(*_middle(field_at(BLOCK)), rect=PLACED)

        assert pointer.press(*_middle(stops_at(BLOCK)[FISHEYE_180_SBS]), rect=PLACED)
        assert verbs.picked == [FISHEYE_180_SBS]
        assert pointer.drawing() == ProjectionControls(FISHEYE_180_SBS, Angle(160.0, 40.0, 240.0))

    def test_a_press_anywhere_else_closes_the_open_list_and_goes_no_further(self):
        pointer, verbs, _showing = _pointer()
        pointer.press(*_middle(field_at(BLOCK)), rect=PLACED)

        assert pointer.press(BLOCK.x + BLOCK.width - 2, BLOCK.y - 2, rect=PLACED)
        assert not pointer.drawing().list_open
        assert verbs.picked == []

    def test_the_open_list_stays_open_while_the_video_changes_under_it(self):
        pointer, verbs, _showing = _pointer()
        pointer.press(*_middle(field_at(BLOCK)), rect=PLACED)

        verbs.projection, verbs.fov = FISHEYE_180_SBS, 150.0

        assert pointer.drawing().list_open

    def test_a_press_off_the_closed_controls_is_left_for_the_console(self):
        pointer, _verbs, _showing = _pointer()

        assert not pointer.press(BLOCK.x + BLOCK.width - 2, BLOCK.y - 2, rect=PLACED)


class TestTheAngleBar:
    def test_a_press_along_it_asks_for_the_angle_at_that_point(self):
        pointer, verbs, _showing = _pointer()
        bar = bar_at(BLOCK)

        assert pointer.press(bar.x + bar.width // 2, bar.y + bar.height // 2, rect=PLACED)
        assert verbs.angles == [pytest.approx(110.0, abs=1.0)]

    def test_a_drag_from_it_goes_on_asking_and_stops_at_either_end(self):
        pointer, verbs, _showing = _pointer()
        bar = bar_at(BLOCK)
        pointer.press(bar.x + bar.width // 2, bar.y + 1, rect=PLACED)

        assert pointer.holding
        assert pointer.drag_to(bar.x - 50, bar.y + 40, rect=PLACED)
        assert pointer.drag_to(bar.x + bar.width + 50, bar.y - 40, rect=PLACED)
        assert verbs.angles[1:] == [40.0, 180.0]

    def test_a_drag_after_letting_go_asks_for_nothing(self):
        pointer, verbs, _showing = _pointer()
        bar = bar_at(BLOCK)
        pointer.press(bar.x + 2, bar.y + 1, rect=PLACED)
        pointer.release()

        assert not pointer.holding
        assert not pointer.drag_to(bar.x + bar.width // 2, bar.y + 1, rect=PLACED)
        assert len(verbs.angles) == 1

    def test_a_projection_without_an_angle_has_no_bar_to_press(self):
        pointer, verbs, _showing = _pointer(projection=FLAT, fov=None)
        bar = bar_at(BLOCK)

        assert not pointer.press(bar.x + bar.width // 2, bar.y + 1, rect=PLACED)
        assert verbs.angles == []


def _painted(controls: ProjectionControls) -> np.ndarray:
    image = Image.new("RGBA", (BLOCK.x + BLOCK.width + 10, BLOCK.y + BLOCK.height + 10),
                      (*BG_PRIMARY, 224))
    controls.draw(image, (BLOCK.x, BLOCK.y, BLOCK.width, BLOCK.height))
    return np.asarray(image)


def _region(painted: np.ndarray, rect: Rect) -> np.ndarray:
    return painted[rect.y:rect.y + rect.height, rect.x:rect.x + rect.width, :3]


def _between_field_and_bar() -> Rect:
    field, bar = field_at(BLOCK), bar_at(BLOCK)
    return Rect(field.x + field.width, BLOCK.y, bar.x - field.x - field.width, BLOCK.height)


class TestWhatItDraws:
    def test_the_field_names_the_projection_the_video_is_shown_in(self):
        field = field_at(BLOCK)

        sbs = _painted(ProjectionControls(EQUIRECT_180_SBS, NARROWED))
        fisheye = _painted(ProjectionControls(FISHEYE_180_SBS, Angle(160.0, 40.0, 240.0)))

        assert not np.array_equal(_region(sbs, field), _region(fisheye, field))

    def test_the_field_wears_the_arrow_every_dropdown_does(self):
        field = field_at(BLOCK)
        painted = _painted(ProjectionControls(EQUIRECT_180_SBS, NARROWED))
        right = painted[field.y + 2:field.y + field.height - 2,
                        field.x + field.width - 14:field.x + field.width - 3, :3]

        assert not np.all(right == np.asarray(BG_BUTTON, dtype=right.dtype))

    def test_the_open_list_hangs_over_what_is_above_the_field(self):
        shown = ProjectionControls(EQUIRECT_180_SBS, NARROWED)
        top = stops_at(BLOCK)[PROJECTIONS[0]]

        closed = _painted(shown)
        opened = _painted(replace(shown, list_open=True))

        assert not np.array_equal(_region(closed, top), _region(opened, top))

    def test_the_open_list_marks_the_projection_the_video_is_shown_in_blue(self):
        opened = _painted(ProjectionControls(FISHEYE_180_SBS, Angle(160.0, 40.0, 240.0),
                                             list_open=True))

        def blue_across(projection: str) -> bool:
            stop = stops_at(BLOCK)[projection]
            band = opened[stop.y + stop.height // 2, stop.x + 2:stop.x + stop.width - 2, :3]
            return bool((band == np.asarray(BLUE, dtype=band.dtype)).all(axis=1).any())

        assert blue_across(FISHEYE_180_SBS)
        assert not blue_across(FLAT)

    def test_the_bar_fills_further_the_wider_the_angle(self):
        bar = bar_at(BLOCK)

        def filled(degrees: float) -> int:
            painted = _painted(ProjectionControls(EQUIRECT_180_SBS, Angle(degrees, 40.0, 180.0)))
            middle = painted[bar.y + bar.height // 2, bar.x:bar.x + bar.width, :3]
            return int((middle == np.asarray(TEXT_PRIMARY, dtype=middle.dtype)).all(axis=1).sum())

        assert filled(40.0) < filled(110.0) < filled(180.0)
        assert filled(180.0) >= bar.width - 2

    def test_the_angle_is_written_beside_its_bar(self):
        narrow = _painted(ProjectionControls(EQUIRECT_180_SBS, Angle(120.0, 40.0, 180.0)))
        wide = _painted(ProjectionControls(EQUIRECT_180_SBS, Angle(160.0, 40.0, 180.0)))

        assert not np.array_equal(_region(narrow, _between_field_and_bar()),
                                  _region(wide, _between_field_and_bar()))

    def test_a_projection_without_an_angle_draws_nothing_after_its_field(self):
        field = field_at(BLOCK)
        rest = Rect(field.x + field.width, BLOCK.y, BLOCK.x + BLOCK.width - field.x - field.width,
                    BLOCK.height)

        region = _region(_painted(ProjectionControls(FLAT, None)), rest)

        assert (region == np.asarray(BG_PRIMARY, dtype=region.dtype)).all()


class TestTheControlsFollowTheMainPlayersVideo:
    def test_they_show_its_projection_at_the_angle_it_was_dialed_to(self):
        pointer, _verbs, _showing = _pointer(EQUIRECT_180_SBS, fov=160.0)

        assert pointer.drawing() == ProjectionControls(EQUIRECT_180_SBS, NARROWED)

    def test_an_angle_never_dialed_is_the_one_the_projection_is_mastered_at(self):
        pointer, _verbs, _showing = _pointer(FISHEYE_180_SBS, fov=None)

        assert pointer.drawing().angle == Angle(180.0, 40.0, 240.0)

    def test_a_screen_and_the_360_have_no_angle(self):
        flat, _flat_verbs, _shown = _pointer(FLAT, fov=None)
        all_round, _round_verbs, _also_shown = _pointer(EQUIRECT_360, fov=None)

        assert flat.drawing().angle is None
        assert all_round.drawing().angle is None

    def test_while_genau_has_the_main_player_there_is_nothing_to_draw(self):
        pointer, _verbs, showing = _pointer()

        showing.now = False

        assert pointer.drawing() is None

    def test_before_the_headsets_verbs_are_made_there_is_nothing_to_draw(self):
        pointer = ProjectionPointer(verbs=lambda: None, showing=lambda: True)

        assert pointer.drawing() is None


class TestBeforeTheConsoleHasPlacedThem:
    def test_they_take_no_press(self):
        pointer, verbs, _showing = _pointer()

        assert not pointer.press(*_middle(field_at(BLOCK)), rect=None)
        assert not pointer.drawing().list_open
        assert verbs.picked == []


class TestWhileGenauHasTheMainPlayer:
    def test_the_controls_take_no_press(self):
        pointer, verbs, showing = _pointer()
        pointer.press(*_middle(field_at(BLOCK)), rect=PLACED)

        showing.now = False
        pointer.drawing()

        assert not pointer.press(*_middle(stops_at(BLOCK)[FISHEYE_180_SBS]), rect=PLACED)
        assert not pointer.press(*_middle(bar_at(BLOCK)), rect=PLACED)
        assert verbs.picked == verbs.angles == []
