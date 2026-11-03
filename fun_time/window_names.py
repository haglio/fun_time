from __future__ import annotations

from shared_ui.preview import Preview, window_title

from .origenerator_switches import RfbMode

# Windows with their own screen rect — always topmost; order among them is
# irrelevant because they never overlap.  The log stream is a child widget of the
# dashboard window, not a window of its own, so it rides the dashboard's band.
# Kino and Genau both run on the Main Funestra's one window.
FIXED_TOPMOST_WINDOWS: tuple[str, ...] = ("portrait", "landscape", "dashboard", "main_funestra")

ORIGENERATOR_WINDOW = "origenerator"

RFB_MODE_WINDOWS: tuple[str, ...] = ("rfb", ORIGENERATOR_WINDOW)

ORIGENERATOR_TITLE = "Origenerator"
_A_PREVIEWS_CAPTION = window_title(ORIGENERATOR_TITLE, Preview(feature=None))


def is_origenerators_caption(caption: str) -> bool:
    return caption == ORIGENERATOR_TITLE or caption.startswith(_A_PREVIEWS_CAPTION)

MANAGED_WINDOWS: tuple[str, ...] = FIXED_TOPMOST_WINDOWS + RFB_MODE_WINDOWS


def window_is_topmost(window_name: str, rfb_mode: RfbMode = RfbMode.RFB) -> bool:
    if window_name not in RFB_MODE_WINDOWS:
        return True
    return (window_name == ORIGENERATOR_WINDOW) == (rfb_mode is RfbMode.ORIGENERATOR)


def visible_windows(rfb_mode: RfbMode = RfbMode.RFB) -> list[str]:
    held = RFB_MODE_WINDOWS if rfb_mode is RfbMode.ORIGENERATOR else ("rfb",)
    return [*FIXED_TOPMOST_WINDOWS, *held]
