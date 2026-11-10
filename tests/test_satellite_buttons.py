from __future__ import annotations

from funestra.hud_icons import MINIMIZE_ICON, shared_icon

from fun_time.satellite_buttons import window_buttons


def test_a_satellites_own_buttons_are_its_minimize_button_and_the_portraits_crown():
    portrait = window_buttons("portrait", in_vr=False, crowned=False)

    assert [button.command for button in portrait] == ["portrait_minimize", "portrait_crown"]
    assert [button.icon for button in portrait] == [MINIMIZE_ICON, shared_icon("crown")]


def test_a_funestra_in_the_headset_has_no_window_to_minimize():
    assert window_buttons("portrait", in_vr=True, crowned=False) == ()


def test_the_portrait_funestras_crown_is_lit_while_it_holds_the_crown():
    held = window_buttons("portrait", in_vr=False, crowned=True)[-1]
    given_away = window_buttons("portrait", in_vr=False, crowned=False)[-1]

    assert (held.command, held.lit, given_away.lit) == ("portrait_crown", True, False)
    assert held.tooltip.startswith("Crowned")


def test_only_the_portrait_funestra_wears_a_crown():
    assert [button.command for button in window_buttons("landscape", in_vr=False, crowned=False)
            ] == ["landscape_minimize"]
