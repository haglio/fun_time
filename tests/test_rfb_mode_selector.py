from __future__ import annotations

import numpy as np
from funestra.hud_button import FIT_THE_WORD, Button
from funestra.satellite_hud import HudModel
from funestra.satellite_hud_paint import HudRenderer

from fun_time import rfb_mode_selector
from fun_time.dashboard_actions import ORIGENERATOR_ACTIVATE, RFB_ACTIVATE
from fun_time.rfb_mode_selector import (
    RfbModeSelector,
    paint_selector,
    place_selector,
    selector_buttons,
)
from fun_time.rfb_modes import RfbMode


def test_the_strip_offers_the_browser_then_origenerator():
    buttons = selector_buttons(RfbMode.RFB, origenerator_core_ready=True)

    assert [(b.command, b.icon, b.width) for b in buttons] == [
        (RFB_ACTIVATE, "Random Faves Browser", FIT_THE_WORD),
        (ORIGENERATOR_ACTIVATE, "Origenerator", FIT_THE_WORD)]


def test_whichever_holds_the_browsers_place_is_lit():
    for holder in RfbMode:
        lit = [b.lit for b in selector_buttons(holder, origenerator_core_ready=True)]
        assert lit == [holder is RfbMode.RFB, holder is RfbMode.ORIGENERATOR], holder


def test_the_origenerator_button_is_dim_until_that_app_is_up():
    starting = selector_buttons(RfbMode.RFB, origenerator_core_ready=False)
    ready = selector_buttons(RfbMode.RFB, origenerator_core_ready=True)

    assert [b.dim for b in starting] == [False, True]
    assert "starting" in starting[1].tooltip
    assert [b.dim for b in ready] == [False, False]
    assert "starting" not in ready[1].tooltip


_CORNERS_BLENDING_INTO_THE_GROUND_PX = 2


def _rgb_inside_the_corners(pixels: np.ndarray, rect) -> np.ndarray:
    x, y, width, height = rect
    inset = _CORNERS_BLENDING_INTO_THE_GROUND_PX
    return pixels[y + inset:y + height - inset, x + inset:x + width - inset, :3]


def test_the_strips_buttons_are_drawn_as_a_huds_word_buttons_are():
    hud = HudRenderer("portrait").render(HudModel(funestra="portrait", rows=((
        Button("portrait_word", "Origenerator", "", width=FIT_THE_WORD, lit=True),),)))
    hud_rect = next(rect for rect, button in hud.targets.buttons
                    if button.icon == "Origenerator")
    hud_rgb = _rgb_inside_the_corners(hud.bgra[:, :, [2, 1, 0, 3]], hud_rect)

    placed = place_selector(selector_buttons(RfbMode.ORIGENERATOR, origenerator_core_ready=True),
                         x=10, y=10)
    strip = np.asarray(paint_selector((400, 38), placed))
    strip_rect = next(rect for rect, button in placed if button.icon == "Origenerator")

    assert (strip_rect[2], strip_rect[3]) == (hud_rect[2], hud_rect[3])
    assert np.array_equal(_rgb_inside_the_corners(strip, strip_rect), hud_rgb)


def test_a_hovered_button_lightens_as_a_huds_does_and_a_dim_one_does_not():
    placed = place_selector(selector_buttons(RfbMode.RFB, origenerator_core_ready=False), x=10, y=10)
    browser, origenerator = (rect for rect, _button in placed)

    def drawn(hovered):
        return np.asarray(paint_selector((400, 38), placed, hovered=hovered))

    assert not np.array_equal(_rgb_inside_the_corners(drawn(browser), browser), _rgb_inside_the_corners(drawn(None), browser))
    assert np.array_equal(_rgb_inside_the_corners(drawn(origenerator), origenerator),
                          _rgb_inside_the_corners(drawn(None), origenerator))


def _counting_paints(monkeypatch) -> list[tuple[int, int]]:
    painted: list[tuple[int, int]] = []
    real = rfb_mode_selector.paint_selector

    def counted(size, placed, **kwargs):
        painted.append(size)
        return real(size, placed, **kwargs)

    monkeypatch.setattr(rfb_mode_selector, "paint_selector", counted)
    return painted


def _a_selector_showing_the_browser() -> RfbModeSelector:
    selector = RfbModeSelector(x=4, y=4, post=lambda _command: None)
    selector.show(RfbMode.RFB, origenerator_core_ready=True)
    return selector


def test_the_strip_is_painted_once_while_nothing_on_it_changes(monkeypatch):
    selector = _a_selector_showing_the_browser()
    painted = _counting_paints(monkeypatch)

    drawings = {selector.rgba((300, 30)) for _ in range(3)}

    assert (len(painted), len(drawings)) == (1, 1)


def test_the_strip_is_painted_again_when_the_pointer_comes_onto_a_button(monkeypatch):
    selector = _a_selector_showing_the_browser()
    painted = _counting_paints(monkeypatch)
    selector.rgba((300, 30))
    (left, top, _width, _height), _button = selector.placed[0]

    selector.move(left + 1, top + 1)
    selector.rgba((300, 30))
    selector.move(left + 2, top + 1)
    selector.rgba((300, 30))

    assert len(painted) == 2


def test_the_strip_says_which_button_the_pointer_is_over_so_a_move_on_it_repaints_nothing():
    selector = _a_selector_showing_the_browser()
    (left, top, _width, _height), _button = selector.placed[0]

    selector.move(left + 1, top + 1)
    first = selector.what_the_pointer_shows()
    selector.move(left + 2, top + 1)

    assert selector.what_the_pointer_shows() == first == selector.placed[0][0]
