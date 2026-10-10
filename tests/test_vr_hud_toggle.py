from __future__ import annotations

import pytest
from player_core.hud_placement import HudEdge

from fun_time_vr.hud_toggle import BUTTON_DEG, PANEL_GAP_DEG, HudToggle, toggle_screen_name
from fun_time_vr.pointer import PRESS, RELEASE, PressEvent
from fun_time_vr.satellite_hud import HUD_GAP_DEG
from fun_time_vr.scene import Placement, attached_to

PICTURE = Placement(azimuth_deg=38.0, elevation_deg=10.0, width_deg=28.0)
ASPECT = 16 / 9


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


def _toggle():
    posted: list[str] = []
    toggle = HudToggle("portrait", "portrait", post=posted.append, mesh=_Mesh(),
                       texture=_Texture(), tip_mesh=_Mesh(), tip_texture=_Texture())
    return toggle, posted


def _hung(toggle: HudToggle, edge: HudEdge = HudEdge.LOWER, *, minimized: bool = False):
    toggle.hang(PICTURE, ASPECT, edge=edge, minimized=minimized)
    button, *_tip = toggle.hangings()
    return button


class TestWhereItHangs:
    @pytest.mark.parametrize("edge", list(HudEdge))
    def test_against_the_side_the_panel_is_on_just_off_the_picture(self, edge):
        toggle, _posted = _toggle()

        button = _hung(toggle, edge)

        assert button.screen.placement == attached_to(
            edge, PICTURE, aspect=ASPECT, width_deg=BUTTON_DEG, hanging_aspect=1.0,
            gap_deg=HUD_GAP_DEG)

    def test_the_minus_and_the_plus_hang_on_one_spot(self):
        toggle, _posted = _toggle()

        minus = _hung(toggle, minimized=False).screen.placement
        plus = _hung(toggle, minimized=True).screen.placement

        assert minus == plus

    @pytest.mark.parametrize("edge", [HudEdge.LEFT, HudEdge.RIGHT])
    def test_the_panel_hangs_past_it_by_the_same_gap(self, edge):
        toggle, _posted = _toggle()
        button = _hung(toggle, edge).screen.placement
        panel = attached_to(edge, PICTURE, aspect=ASPECT, width_deg=20.0,
                            hanging_aspect=1.0, gap_deg=PANEL_GAP_DEG)

        outward = 1 if edge is HudEdge.RIGHT else -1
        button_far = button.azimuth_deg + outward * button.width_deg / 2
        panel_near = panel.azimuth_deg - outward * panel.width_deg / 2

        assert outward * (panel_near - button_far) == pytest.approx(HUD_GAP_DEG)

    def test_it_comes_forward_with_the_picture_it_hangs_beside(self):
        toggle, _posted = _toggle()

        assert {one.forward_with for one in (_hung(toggle),)} == {"portrait"}


class TestASqueezeOnIt:
    def test_on_the_minus_minimizes_the_panel(self):
        toggle, posted = _toggle()
        _hung(toggle, minimized=False)

        took = toggle.take(PressEvent(PRESS, toggle_screen_name("portrait")))

        assert took
        assert posted == ["portrait_hud_minimize"]

    def test_on_the_plus_opens_the_panel(self):
        toggle, posted = _toggle()
        _hung(toggle, minimized=True)

        toggle.take(PressEvent(PRESS, toggle_screen_name("portrait")))

        assert posted == ["portrait_hud_restore"]

    def test_letting_go_asks_for_nothing_more(self):
        toggle, posted = _toggle()
        _hung(toggle)

        assert toggle.take(PressEvent(RELEASE, toggle_screen_name("portrait")))
        assert posted == []

    def test_anywhere_else_is_not_its_to_take(self):
        toggle, posted = _toggle()
        _hung(toggle)

        assert not toggle.take(PressEvent(PRESS, "portrait"))
        assert posted == []


class TestWhatItSays:
    def test_its_name_shows_beside_it_only_while_it_is_pointed_at(self):
        toggle, _posted = _toggle()
        _hung(toggle)
        assert len(toggle.hangings()) == 1

        toggle.point(toggle_screen_name("portrait"))
        _hung(toggle)

        button, tip = toggle.hangings()
        assert button.screen.pressable and not tip.screen.pressable

    def test_it_is_painted_once_while_nothing_about_it_changes(self):
        texture = _Texture()
        toggle = HudToggle("portrait", "portrait", post=lambda _command: None, mesh=_Mesh(),
                           texture=texture, tip_mesh=_Mesh(), tip_texture=_Texture())

        _hung(toggle)
        _hung(toggle)

        assert texture.uploads == 1

    def test_a_slot_with_nothing_to_hang_beside_hangs_none(self):
        toggle, _posted = _toggle()
        _hung(toggle)

        toggle.unhang()

        assert toggle.hangings() == ()
