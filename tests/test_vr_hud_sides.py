from __future__ import annotations

import math

from player_core.hud_placement import HudEdge

from fun_time_vr.hud_sides import HudSides, side_named, side_screen_name, sides
from fun_time_vr.pointer import HANDLE_DEG, PRESS, RELEASE, PressEvent
from fun_time_vr.scene import RADIUS, Placement, center_height, half_width

PICTURE = Placement(azimuth_deg=38.0, elevation_deg=10.0, width_deg=28.0)


def _sides(panel_edge: HudEdge = HudEdge.LOWER, *, minimized: bool = False):
    return sides("portrait", "portrait", PICTURE, 16 / 9,
                 panel_edge=panel_edge, minimized=minimized)


class TestTheSidesThePanelIsNotAgainst:
    def test_there_is_one_beside_each_of_the_other_three(self):
        assert {side.edge for side in _sides(HudEdge.LEFT)} == {
            HudEdge.UPPER, HudEdge.RIGHT, HudEdge.LOWER}

    def test_each_is_a_screen_the_pointer_can_press(self):
        for side in _sides():
            assert side.screen.pressable
            assert side_named(side.screen.name) is side.edge

    def test_the_one_above_hangs_clear_of_the_strip_a_squeeze_moves_the_picture_by(self):
        upper, = (side for side in _sides() if side.edge is HudEdge.UPPER)
        placement = upper.screen.placement

        side_bottom = center_height(placement) - half_width(placement.width_deg) / upper.screen.aspect
        picture_top = center_height(PICTURE) + half_width(PICTURE.width_deg) / (16 / 9)
        assert side_bottom >= picture_top + RADIUS * math.radians(HANDLE_DEG)


class TestAPlayersSides:
    @staticmethod
    def _sides():
        posted: list[str] = []
        return HudSides("landscape", "landscape", post=posted.append), posted

    def test_a_squeeze_on_one_asks_for_the_panel_there_open(self):
        hud_sides, posted = self._sides()

        took = hud_sides.take(PressEvent(PRESS, side_screen_name("landscape", HudEdge.LEFT)))

        assert took
        assert posted == ["landscape_hud_restore_at|left"]

    def test_letting_go_of_one_asks_for_nothing_more(self):
        hud_sides, posted = self._sides()

        took = hud_sides.take(PressEvent(RELEASE, side_screen_name("landscape", HudEdge.LEFT)))

        assert took
        assert posted == []

    def test_a_squeeze_anywhere_else_is_not_its_to_take(self):
        hud_sides, posted = self._sides()

        assert not hud_sides.take(PressEvent(PRESS, "landscape"))
        assert posted == []

    def test_the_side_pointed_at_is_the_one_whose_plus_shows(self):
        hud_sides, _posted = self._sides()

        hud_sides.point(side_screen_name("landscape", HudEdge.UPPER))
        assert hud_sides.pointed_at is HudEdge.UPPER

        hud_sides.point("landscape")
        assert hud_sides.pointed_at is None


class _Texture:
    def __init__(self) -> None:
        self.uploads = 0

    def upload(self, _rgba) -> None:
        self.uploads += 1


class _Mesh:
    def __init__(self) -> None:
        self.hung_at = None

    def rehang_at(self, placement, aspect) -> None:
        self.hung_at = (placement, aspect)


class TestHangingThem:
    @staticmethod
    def _sides():
        texture, mesh = _Texture(), _Mesh()
        hud_sides = HudSides("portrait", "portrait", post=lambda _command: None,
                             mesh=mesh, texture=texture)
        return hud_sides, texture, mesh

    @staticmethod
    def _hang(hud_sides) -> None:
        hud_sides.hang(PICTURE, 16 / 9, panel_edge=HudEdge.LOWER, minimized=False)

    def test_only_the_side_pointed_at_is_drawn(self):
        hud_sides, texture, mesh = self._sides()
        hud_sides.point(side_screen_name("portrait", HudEdge.RIGHT))

        self._hang(hud_sides)

        drawn = {side_named(one.screen.name): one.mesh is mesh and one.picture is texture
                 for one in hud_sides.hangings()}
        assert drawn == {HudEdge.UPPER: False, HudEdge.RIGHT: True, HudEdge.LEFT: False}

    def test_every_side_comes_forward_with_the_picture_it_hangs_beside(self):
        hud_sides, _texture, _mesh = self._sides()

        self._hang(hud_sides)

        assert {one.forward_with for one in hud_sides.hangings()} == {"portrait"}

    def test_the_plus_is_painted_once_while_it_stays_pointed_at(self):
        hud_sides, texture, _mesh = self._sides()
        hud_sides.point(side_screen_name("portrait", HudEdge.RIGHT))

        self._hang(hud_sides)
        self._hang(hud_sides)

        assert texture.uploads == 1

    def test_a_slot_with_no_rectangle_to_hang_beside_hangs_none(self):
        hud_sides, _texture, _mesh = self._sides()
        self._hang(hud_sides)

        hud_sides.unhang()

        assert hud_sides.hangings() == ()
