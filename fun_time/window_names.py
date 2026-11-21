from __future__ import annotations

from shared_ui.preview import Preview, window_title

from .rfb_modes import RfbMode
from .win32 import name_the_window

FIXED_TOPMOST_WINDOWS: tuple[str, ...] = ("portrait", "landscape", "dashboard", "main_funestra")

ORIGENERATOR_WINDOW = "origenerator"

RFB_MODE_WINDOWS: tuple[str, ...] = ("rfb", ORIGENERATOR_WINDOW)

ORIGENERATOR_TITLE = "Origenerator"
_A_PREVIEWS_CAPTION = window_title(ORIGENERATOR_TITLE, Preview(feature=None))


def is_origenerators_caption(caption: str) -> bool:
    return caption == ORIGENERATOR_TITLE or caption.startswith(_A_PREVIEWS_CAPTION)

MANAGED_WINDOWS: tuple[str, ...] = FIXED_TOPMOST_WINDOWS + RFB_MODE_WINDOWS

WHAT_THE_RECORD_CALLS_EACH_WINDOW: dict[str, str] = {
    "main_funestra": "the Main Funestra",
    "portrait": "the Portrait Satellite",
    "landscape": "the Landscape Satellite",
    "dashboard": "the Dashboard",
    "rfb": "the Random Faves Browser",
    ORIGENERATOR_WINDOW: "Origenerator Core",
}


def name_in_the_record(window_name: str, hwnd: int) -> None:
    name_the_window(hwnd, WHAT_THE_RECORD_CALLS_EACH_WINDOW[window_name])


def window_is_topmost(window_name: str, rfb_mode: RfbMode = RfbMode.RFB) -> bool:
    if window_name not in RFB_MODE_WINDOWS:
        return True
    return (window_name == ORIGENERATOR_WINDOW) == (rfb_mode is RfbMode.ORIGENERATOR)


def visible_windows(rfb_mode: RfbMode = RfbMode.RFB) -> list[str]:
    the_browser_and_what_covers_it = (
        RFB_MODE_WINDOWS if rfb_mode is RfbMode.ORIGENERATOR else ("rfb",))
    return [*FIXED_TOPMOST_WINDOWS, *the_browser_and_what_covers_it]
