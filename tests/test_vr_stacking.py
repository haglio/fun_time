from __future__ import annotations

from fun_time_vr import stacking
from fun_time_vr.pointer import Screen
from fun_time_vr.scene import Placement
from fun_time_vr.stacking import Pane, Stacking


def _screen(name: str, azimuth_deg: float = 0.0, *, elevation_deg: float = 0.0,
            width_deg: float = 28.0) -> Screen:
    return Screen(name, Placement(azimuth_deg, elevation_deg, width_deg), aspect=16 / 9)


def _names(screens) -> list[str]:
    return [screen.name for screen in screens]


def test_panes_nobody_has_taken_hold_of_stand_in_the_order_given():
    panes = [Pane((_screen("main", width_deg=72.0),)), Pane((_screen("landscape", -38.0),))]

    assert _names(Stacking().arrange(panes)) == ["main", "landscape"]


def test_the_pane_last_taken_hold_of_comes_to_the_front():
    stacking = Stacking()
    panes = [Pane((_screen("main", width_deg=72.0),)), Pane((_screen("landscape", -38.0),)),
             Pane((_screen("portrait", 38.0),))]

    stacking.take("main")
    main_first = _names(stacking.arrange(panes))
    stacking.take("landscape")

    assert main_first == ["landscape", "portrait", "main"]
    assert _names(stacking.arrange(panes)) == ["portrait", "main", "landscape"]


def test_taking_hold_of_what_hangs_off_a_pane_brings_the_whole_pane_forward():
    stacking = Stacking()
    hud = _screen("landscape/hud", -38.0, elevation_deg=-4.0, width_deg=20.0)
    panes = [Pane((_screen("landscape", -38.0), hud)), Pane((_screen("main", width_deg=72.0),))]

    stacking.take("landscape/hud")

    assert _names(stacking.arrange(panes)) == ["main", "landscape", "landscape/hud"]


def test_a_pane_taken_hold_of_leaves_one_it_covers_completely_in_front():
    stacking = Stacking()
    inside = Pane((_screen("portrait", 20.0, width_deg=20.0),))
    across_the_edge = Pane((_screen("landscape", -38.0),))

    stacking.take("main")

    assert _names(stacking.arrange([inside, across_the_edge, Pane((_screen("main", width_deg=72.0),))])) == [
        "landscape", "main", "portrait"]


def test_two_panes_hanging_in_the_very_same_place_stand_as_they_were_taken():
    stacking = Stacking()
    panes = [Pane((_screen("landscape", -38.0),)), Pane((_screen("portrait", -38.0),))]

    stacking.take("portrait")
    stacking.take("landscape")

    assert _names(stacking.arrange(panes)) == ["portrait", "landscape"]


def test_panes_that_have_not_moved_and_nobody_has_taken_are_not_weighed_again(monkeypatch):
    weighed: list[int] = []
    encloses = stacking.encloses
    monkeypatch.setattr(stacking, "encloses", lambda *a, **k: weighed.append(1) or encloses(*a, **k))
    order = Stacking()
    panes = [Pane((_screen("main"),)), Pane((_screen("dash", 10.0),))]

    first = order.arrange(panes)
    once = len(weighed)
    again = [order.arrange(list(panes)) for _ in range(5)]

    assert once > 0
    assert len(weighed) == once
    assert all(_names(arranged) == _names(first) for arranged in again)


def test_a_pane_moved_or_taken_is_weighed_again():
    order = Stacking()
    main, dash = Pane((_screen("main"),)), Pane((_screen("dash", 10.0),))
    order.arrange([main, dash])

    order.take("main")
    taken = order.arrange([main, dash])
    moved = order.arrange([main, Pane((_screen("dash", 12.0),))])

    assert _names(taken) == ["dash", "main"]
    assert _names(moved) == ["dash", "main"]
