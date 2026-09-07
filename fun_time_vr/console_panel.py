"""The main console, hanging in the headset -- what to put on it."""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import replace

from PIL import Image, ImageDraw, ImageFont
from player_core.console import tooltip_at
from player_core.console_hud import (
    ConsoleHud,
    ConsolePainter,
    ModeHud,
    hud_xy,
    with_playback_speed,
)
from shared_ui.palette import AMBER, BG_PRIMARY, GREEN, RED, TEXT_MUTED, TEXT_PRIMARY

from fun_time.event_log import FAVORITE, NOTICE
from fun_time.mode_plan import nau_displays

from .notices import KEPT, Notice
from .pointer import surface_pixel

# Pixels across, held: the screen keeps one size between the modes (the genau
# rows are narrower) and across titles (elided), so its bitmap never rescales.
PANEL_WIDTH_PX = 280
PANEL_WIDTH_DEG = 24.0  # its fixed angular width; it docks under the main player
DEG_PER_PX = PANEL_WIDTH_DEG / PANEL_WIDTH_PX  # every control in the scene, one size

# Segoe UI Bold, the face every HUD here is read at a glance in, at 9pt.
_NOTICE_FONT_PX = 12
_NOTICE_ROW_H = 15
_NOTICE_PAD = 4

# Held: one height whether or not anything is on it, so the panel never resizes.
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


def paint_panel(
    painter,
    hud: ConsoleHud,
    *,
    hover: tuple[int, int] | None = None,
    notices: Sequence[Notice] | None = (),
    row=None,
) -> Image.Image:
    # Strip over the console, *row* under it, ``notices=None`` for no strip at all --
    # which an empty one is not: it holds its height, and reads there as a gap.
    console_rgba, console_size = painter.rgba(hud, hover=hover)
    console = Image.frombytes("RGBA", console_size, console_rgba)
    strip = None if notices is None else paint_notices(notices, console.width)
    top = 0 if strip is None else strip.height
    tall = top + console.height + (0 if row is None else row.shape[0])
    panel = Image.new("RGBA", (console.width, tall), (0, 0, 0, 0))
    if strip is not None:
        panel.alpha_composite(strip, (0, 0))
    panel.alpha_composite(console, (0, top))
    if row is not None:
        panel.alpha_composite(Image.fromarray(row, "RGBA"), (0, top + console.height))
    return panel


class PanelPointer:
    def __init__(self, painter: ConsolePainter, *, post: Callable[[str], None]) -> None:
        self._painter = painter
        self._post = post
        self._size = (1, 1)
        self._strip = NOTICE_STRIP_HEIGHT
        self._tip: tuple[str, tuple[int, int]] | None = None

    def painted(self, size: tuple[int, int], *, strip_height: int = NOTICE_STRIP_HEIGHT
                ) -> None:
        self._size, self._strip = size, strip_height

    def _pixel(self, u: float, v: float) -> tuple[int, int]:
        return surface_pixel(u, v, self._size)

    def _console_pixel(self, u: float, v: float) -> tuple[int, int]:
        """The same point in the CONSOLE's pixels, which the strip pushed down."""
        px, py = self._pixel(u, v)
        return px, py - self._strip

    def press(self, u: float, v: float) -> None:
        self.release()
        px, py = self._console_pixel(u, v)
        left, top = hud_xy()
        command = self._painter.press_at(px + left, py + top)
        if command:
            self._post(command)

    def drag(self, u: float, v: float) -> None:
        if self._painter.holding:
            px, py = self._pixel(u, v)
            left, top = hud_xy()
            _, cy = self._console_pixel(u, v)
            command = self._painter.drag_to(px + left, cy + top)
            if command:
                self._post(command)

    def release(self) -> None:
        self._painter.release()

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
