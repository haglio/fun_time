from __future__ import annotations

import numpy as np
from funestra.hud_button import FIT_THE_WORD, Button
from funestra.satellite_hud import HudModel
from funestra.satellite_hud_paint import HudRenderer

from fun_time.dashboard_actions import ORIGENERATOR_ACTIVATE, RFB_ACTIVATE
from fun_time.rfb_mode_selector import paint_selector, place_selector, selector_buttons
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
