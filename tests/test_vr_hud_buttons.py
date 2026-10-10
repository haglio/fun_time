from __future__ import annotations

from player_core.hud_placement import HudEdge

from fun_time_vr.hud_buttons import HudButtons, hud_button_names
from fun_time_vr.hud_sides import side_screen_name
from fun_time_vr.hud_toggle import toggle_screen_name
from fun_time_vr.pointer import PRESS, PressEvent
from fun_time_vr.scene import Placement

PICTURE = Placement(azimuth_deg=38.0, elevation_deg=10.0, width_deg=28.0)
ASPECT = 16 / 9


class _Made:
    def __init__(self) -> None:
        self.closed = False

    def upload(self, _rgba) -> None:
        pass

    def rehang_at(self, _placement, _aspect) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _buttons():
    posted: list[str] = []
    made: list[_Made] = []

    def make() -> _Made:
        made.append(_Made())
        return made[-1]

    buttons = HudButtons("portrait", "portrait", post=posted.append, texture=make, mesh=make)
    return buttons, posted, made


def _hanging_names(buttons: HudButtons) -> set[str]:
    return {hanging.screen.name for hanging in buttons.hangings()}


def test_beside_a_picture_the_minus_hangs_with_a_plus_beside_each_other_side():
    buttons, _posted, _made = _buttons()

    buttons.hang(PICTURE, ASPECT, edge=HudEdge.LEFT, minimized=False)

    assert _hanging_names(buttons) == {
        toggle_screen_name("portrait"),
        *(side_screen_name("portrait", edge)
          for edge in (HudEdge.UPPER, HudEdge.RIGHT, HudEdge.LOWER)),
    }


def test_with_no_side_to_move_to_only_the_minus_hangs():
    buttons, _posted, _made = _buttons()
    buttons.hang(PICTURE, ASPECT, edge=HudEdge.LEFT, minimized=False)

    buttons.hang(PICTURE, ASPECT, edge=HudEdge.LOWER, minimized=False, with_sides=False)

    assert _hanging_names(buttons) == {toggle_screen_name("portrait")}


def test_taken_down_nothing_of_it_hangs():
    buttons, _posted, _made = _buttons()
    buttons.hang(PICTURE, ASPECT, edge=HudEdge.LEFT, minimized=False)

    buttons.unhang()

    assert _hanging_names(buttons) == set()


def test_everything_it_hangs_answers_to_a_name_it_is_known_by():
    buttons, _posted, _made = _buttons()

    buttons.hang(PICTURE, ASPECT, edge=HudEdge.UPPER, minimized=False)

    assert _hanging_names(buttons) <= set(hud_button_names("portrait"))


def test_a_squeeze_on_the_minus_or_a_side_is_its_to_take_and_nothing_else_is():
    buttons, posted, _made = _buttons()

    took = [buttons.take(PressEvent(PRESS, name)) for name in (
        toggle_screen_name("portrait"), side_screen_name("portrait", HudEdge.RIGHT), "portrait")]

    assert took == [True, True, False]
    assert posted == ["portrait_hud_minimize", "portrait_hud_restore_at|right"]


def test_closing_it_closes_everything_it_made():
    buttons, _posted, made = _buttons()

    buttons.close()

    assert made
    assert all(each.closed for each in made)
