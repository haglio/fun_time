"""The main console, hanging in the headset -- what to put on it.

The desktop paints it onto the main player's window; baked into an immersive
video it would warp with it, so here it is a small screen of its own.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from player_core.console import tooltip_at
from player_core.console_hud import (
    ConsoleHud,
    ConsolePainter,
    ModeHud,
    hud_xy,
    with_playback_speed,
)
from player_core.timeline import TIMELINE_HEIGHT, progress_bar_bgra
from player_core.volume import VolumeHud, chip_local, chip_xy, hit_part, volume_at
from shared_ui.palette import AMBER, BG_PRIMARY, GREEN, RED, TEXT_MUTED, TEXT_PRIMARY

from fun_time.event_log import FAVORITE, NOTICE
from fun_time.mode_plan import nau_displays
from satellite.pointer import time_at

from .notices import KEPT, Notice
from .pointer import surface_pixel

# Pixels across, held: the screen keeps one size between the modes and across
# titles -- its angular width is fixed, so a bitmap that changed width would
# rescale it all.
PANEL_WIDTH_PX = 280

_ROW_GAP = 6

# Segoe UI Bold, the face every HUD here is read at a glance in, at 9pt.
_NOTICE_FONT_PX = 12
_NOTICE_ROW_H = 15
_NOTICE_PAD = 4

# Held, the way the furniture row below is.
NOTICE_STRIP_HEIGHT = KEPT * _NOTICE_ROW_H + _NOTICE_PAD

# fun_time.log_panel's own mapping.
_LEVEL_COLORS: dict[int, tuple[int, int, int]] = {
    NOTICE: TEXT_PRIMARY,
    FAVORITE: GREEN,
    logging.WARNING: AMBER,
    logging.ERROR: RED,
}


def level_color(level: int) -> tuple[int, int, int]:
    """The color for *level*, rounding down to the loudest level it reaches."""
    for threshold in sorted(_LEVEL_COLORS, reverse=True):
        if level >= threshold:
            return _LEVEL_COLORS[threshold]
    return TEXT_MUTED


def _notice_font() -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("segoeuib.ttf", _NOTICE_FONT_PX)
    except OSError:
        return ImageFont.load_default(_NOTICE_FONT_PX)


def fit_notice(font, text: str, width: int) -> str:
    """*text* if it draws inside *width*, else its head with an ellipsis."""
    if font.getlength(text) <= width or not text:
        return text
    kept = text
    while kept and font.getlength(kept + "…") > width:
        kept = kept[:-1]
    return kept + "…"


def paint_notices(notices: Sequence[Notice], width: int) -> Image.Image:
    """The strip above the console, newest lowest -- always NOTICE_STRIP_HEIGHT
    tall and transparent where there is nothing to say."""
    strip = Image.new("RGBA", (width, NOTICE_STRIP_HEIGHT), (0, 0, 0, 0))
    if not notices:
        return strip
    font = _notice_font()
    draw = ImageDraw.Draw(strip)
    inner = width - 2 * _NOTICE_PAD
    rows = list(notices)[-KEPT:]
    top = NOTICE_STRIP_HEIGHT - _NOTICE_PAD - len(rows) * _NOTICE_ROW_H
    for index, one in enumerate(rows):
        y = top + index * _NOTICE_ROW_H
        draw.rounded_rectangle(
            (0, y, width - 1, y + _NOTICE_ROW_H - 1), radius=4, fill=(*BG_PRIMARY, 224),
        )
        draw.text(
            (_NOTICE_PAD, y + 1), fit_notice(font, one.message, inner),
            font=font, fill=(*level_color(one.level), 255),
        )
    return strip


def panel_painter() -> ConsolePainter:
    """The desktop's console painter, held to the panel's one width."""
    return ConsolePainter(width=PANEL_WIDTH_PX)


def panel_hud(
    engine_hud: ConsoleHud | None,
    *,
    video_title: str,
    clip_title: str,
    loading: str | None,
    drive_gate,
    f_mode: bool = False,
    playback_speed: float = 1.0,
) -> ConsoleHud:
    """The engine's console re-said for the mode: the video's name on top and
    the funscript folded into the readout by *drive_gate*
    (:class:`player_core.drive_gate.DriveGate`) under a video, as the desktop's
    video-mode console draws it; the clip's name (or the one still decoding)
    over Genau's own motion in genau mode, where the gate is told nothing was
    published, the video waiting paused while the wave moves on.  With no
    engine console (the broker has the room) the panel still names what plays.

    *f_mode* is the main player's own, folded in as Nau folds in its own: the
    published console lights the F button, the line beside it is the drawing
    player's, and in genau mode that slot is Genau's filters'.
    """
    hud = engine_hud if engine_hud is not None else ConsoleHud()
    if nau_displays(hud.console.mode):
        title, drive = video_title, drive_gate.readout(hud.drive)
    else:
        drive_gate.readout(None)
        title, drive = loading or clip_title, hud.drive
    return replace(
        hud,
        modes=ModeHud(video=title, f_mode=f_mode and nau_displays(hud.console.mode)),
        drive=drive,
        console=with_playback_speed(hud.console, playback_speed),
    )


def _rgba(bgra: np.ndarray) -> Image.Image:
    return Image.fromarray(np.ascontiguousarray(bgra[:, :, [2, 1, 0, 3]]), "RGBA")


def paint_panel(
    painter,
    hud: ConsoleHud,
    *,
    scrubber: tuple[float, float] | None,
    chip: VolumeHud,
    chip_painter,
    hover: tuple[int, int] | None = None,
    notices: Sequence[Notice] = (),
) -> Image.Image:
    """The console with the announcement strip over it and the furniture row
    under it: the scrubber, given ``(position_ms, duration_ms)`` (None for a clip,
    which loops -- the row keeps its height), and the chip at its right end."""
    console_rgba, console_size = painter.rgba(hud, hover=hover)
    console = Image.frombytes("RGBA", console_size, console_rgba)
    chip_image = _rgba(chip_painter.bgra(chip))
    width = console.width
    row_h = max(TIMELINE_HEIGHT, chip_image.height)
    strip = paint_notices(notices, width)
    height = strip.height + console.height + _ROW_GAP + row_h
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel.alpha_composite(strip, (0, 0))
    panel.alpha_composite(console, (0, strip.height))
    row_top = strip.height + console.height + _ROW_GAP
    if scrubber is not None:
        position_ms, duration_ms = scrubber
        bar = _rgba(progress_bar_bgra(position_ms, duration_ms, None, width))
        panel.alpha_composite(bar, (0, height - bar.height))
    x, y = chip_xy(win_w=width, win_h=height, timeline_h=TIMELINE_HEIGHT)
    panel.alpha_composite(chip_image, (max(0, x), max(row_top, y)))
    return panel


class PanelPointer:
    def __init__(
        self, painter: ConsolePainter, *, post: Callable[[str], None],
        seek: Callable[[float], None],
    ) -> None:
        self._painter = painter
        self._post = post
        self._seek = seek
        self._size = (1, 1)
        self._scrubber: tuple[float, float] | None = None
        self._chip = VolumeHud()
        self._sliding_volume = False
        self._asked_volume = ""
        self._tip: tuple[str, tuple[int, int]] | None = None

    def painted(
        self, size: tuple[int, int], *, scrubber: tuple[float, float] | None, chip: VolumeHud,
    ) -> None:
        self._size, self._scrubber, self._chip = size, scrubber, chip

    def _pixel(self, u: float, v: float) -> tuple[int, int]:
        return surface_pixel(u, v, self._size)

    def _console_pixel(self, u: float, v: float) -> tuple[int, int]:
        """The same point in the CONSOLE's pixels, which the strip pushed down."""
        px, py = self._pixel(u, v)
        return px, py - NOTICE_STRIP_HEIGHT

    def _chip_part(self, px: int, py: int) -> tuple[str, int]:
        width, height = self._size
        cx, cy = chip_local(px, py, win_w=width, win_h=height, timeline_h=TIMELINE_HEIGHT)
        return hit_part(cx, cy), cx

    def _slide_volume(self, cx: int) -> None:
        command = f"audio_set_volume|{volume_at(cx)}"
        if command != self._asked_volume:
            self._asked_volume = command
            self._post(command)

    def press(self, u: float, v: float) -> None:
        self.release()
        px, py = self._pixel(u, v)
        part, cx = self._chip_part(px, py)
        if part == "mute":
            self._post("audio_unmute" if self._chip.muted else "audio_mute")
        elif part == "track":
            self._sliding_volume = True
            self._slide_volume(cx)
        elif self._scrubber is not None and py >= self._size[1] - TIMELINE_HEIGHT:
            self._seek(time_at(px, win_w=self._size[0], duration_ms=self._scrubber[1]))
        else:
            left, top = hud_xy()
            _, cy = self._console_pixel(u, v)
            command = self._painter.press_at(px + left, cy + top)
            if command:
                self._post(command)

    def drag(self, u: float, v: float) -> None:
        px, py = self._pixel(u, v)
        if self._sliding_volume:
            self._slide_volume(self._chip_part(px, py)[1])
        elif self._painter.holding:
            left, top = hud_xy()
            _, cy = self._console_pixel(u, v)
            command = self._painter.drag_to(px + left, cy + top)
            if command:
                self._post(command)

    def release(self) -> None:
        self._painter.release()
        self._sliding_volume, self._asked_volume = False, ""

    def tooltip_anchor(self, uv: tuple[float, float] | None) -> tuple[int, int] | None:
        """The tooltip's anchor, in the CONSOLE's pixels -- where its buttons are."""
        tip = ""
        if uv is not None and 0.0 <= uv[0] <= 1.0 and 0.0 <= uv[1] <= 1.0:
            px, py = self._console_pixel(*uv)
            tip = tooltip_at(self._painter.buttons, px, py)
        if not tip:
            self._tip = None
        elif self._tip is None or self._tip[0] != tip:
            self._tip = (tip, (px, py))
        return self._tip[1] if self._tip is not None else None
