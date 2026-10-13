"""The strip under the dashboard's log, which says what holds the Random Favs
Browser's place -- drawn by the hand that draws a Satellite Funestra's own
Kino / Origenerator pair, so the two read as one control."""
from __future__ import annotations

import numpy as np
from funestra.hud_button import FIT_THE_WORD
from funestra.satellite_hud import HudModel
from funestra.satellite_hud_paint import HudRenderer

from fun_time.dashboard_actions import ORIGENERATOR_ACTIVATE, RFB_ACTIVATE
from fun_time.origenerator_switches import ORIGENERATOR_MODE, RfbSlot
from fun_time.rfb_slot_strip import paint_strip, place_strip, strip_buttons
from fun_time.satellite_buttons import mode_row


def test_the_strip_offers_the_browser_then_origenerator():
    buttons = strip_buttons(RfbSlot.RFB, origenerator_ready=True)

    assert [(b.command, b.glyph, b.width) for b in buttons] == [
        (RFB_ACTIVATE, "Random Favs Browser", FIT_THE_WORD),
        (ORIGENERATOR_ACTIVATE, "Origenerator", FIT_THE_WORD)]


def test_whichever_holds_the_browsers_place_is_lit():
    for holder in RfbSlot:
        lit = [b.lit for b in strip_buttons(holder, origenerator_ready=True)]
        assert lit == [holder is RfbSlot.RFB, holder is RfbSlot.ORIGENERATOR], holder


def test_the_origenerator_button_is_dim_until_that_app_is_up():
    """As a Satellite Funestra's is: faded, never pressed, and saying on hover
    what it waits for."""
    starting = strip_buttons(RfbSlot.RFB, origenerator_ready=False)
    ready = strip_buttons(RfbSlot.RFB, origenerator_ready=True)

    assert [b.dim for b in starting] == [False, True]
    assert "starting" in starting[1].tooltip
    assert [b.dim for b in ready] == [False, False]
    assert "starting" not in ready[1].tooltip


def _rgb(pixels: np.ndarray, rect, *, inset: int = 2) -> np.ndarray:
    x, y, width, height = rect
    return pixels[y + inset:y + height - inset, x + inset:x + width - inset, :3]


def test_the_strips_buttons_are_drawn_as_a_satellites_mode_pair_is():
    """The same ground, edge, corner, face and width, pixel for pixel inside
    the edge -- the corners alone blend into what each is drawn over."""
    hud = HudRenderer("portrait").render(HudModel(
        funestra="portrait", rows=(mode_row("portrait", mode=ORIGENERATOR_MODE),)))
    hud_rect = next(rect for rect, button in hud.targets.buttons
                    if button.glyph == "Origenerator")
    hud_rgb = _rgb(hud.bgra[:, :, [2, 1, 0, 3]], hud_rect)

    placed = place_strip(strip_buttons(RfbSlot.ORIGENERATOR, origenerator_ready=True),
                         x=10, y=10)
    strip = np.asarray(paint_strip((400, 38), placed))
    strip_rect = next(rect for rect, button in placed if button.glyph == "Origenerator")

    assert (strip_rect[2], strip_rect[3]) == (hud_rect[2], hud_rect[3])
    assert np.array_equal(_rgb(strip, strip_rect), hud_rgb)


def test_a_hovered_button_lightens_as_a_huds_does_and_a_dim_one_does_not():
    placed = place_strip(strip_buttons(RfbSlot.RFB, origenerator_ready=False), x=10, y=10)
    browser, origenerator = (rect for rect, _button in placed)

    def drawn(hovered):
        return np.asarray(paint_strip((400, 38), placed, hovered=hovered))

    assert not np.array_equal(_rgb(drawn(browser), browser), _rgb(drawn(None), browser))
    assert np.array_equal(_rgb(drawn(origenerator), origenerator),
                          _rgb(drawn(None), origenerator))
