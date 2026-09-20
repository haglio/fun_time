"""The main console, hanging in the headset -- what to put on it."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

from PIL import Image
from player_core.console import tooltip_at
from player_core.console_hud import (
    ConsoleHud,
    ConsolePainter,
    ModeHud,
    hud_xy,
    with_playback_speed,
)
from player_core.hud_placement import HudEdge
from shared_ui.palette import AMBER, GREEN, RED, TEXT_MUTED, TEXT_PRIMARY

from fun_time.event_log import FAVORITE, NOTICE
from fun_time.mode_plan import main_player_displays

from .pointer import surface_pixel

# Pixels across, held: the screen keeps one size between the modes (the genau
# rows are narrower) and across titles (elided), so its bitmap never rescales.
PANEL_WIDTH_PX = 380
PANEL_WIDTH_DEG = 32.6
DEG_PER_PX = PANEL_WIDTH_DEG / PANEL_WIDTH_PX  # every control in the scene, one size

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


def panel_painter() -> ConsolePainter:
    """The desktop's console painter, held to the panel's one width."""
    return ConsolePainter(width=PANEL_WIDTH_PX)


def panel_hangs_from(edge: HudEdge, *, wrapped: bool) -> HudEdge:
    return HudEdge.LOWER if wrapped else edge


def panel_hud(
    engine_hud: ConsoleHud | None,
    *,
    video_title: str,
    clip_title: str,
    loading: str | None,
    drive_gate,
    scripted_filter: bool = False,
    playback_speed: float = 1.0,
) -> ConsoleHud:
    """The engine's console re-said for the mode: under a video, its name on top
    and the funscript folded into the readout by *drive_gate*
    (:class:`player_core.drive_gate.DriveGate`), as the desktop's video-mode
    console draws it; in genau mode the clip's name (or the one still decoding)
    over Genau's own motion, the gate told nothing was published.  Before the
    engine's first tick there is none, and the panel still names what plays.

    *scripted_filter* is the main player's own: the published console lights the
    F button, the line beside it is the drawing player's, and in genau mode that
    slot is Genau's filters'.
    """
    hud = engine_hud if engine_hud is not None else ConsoleHud()
    drives_itself = hud.console.device_drives_itself
    if main_player_displays(hud.console.main_mode):
        title = video_title
        drive = drive_gate.readout(hud.drive,
                                   device_drives_itself=drives_itself)
    else:
        drive_gate.readout(None, device_drives_itself=drives_itself)
        title, drive = loading or clip_title, hud.drive
    return replace(
        hud,
        modes=ModeHud(video=title, scripted_filter=scripted_filter and main_player_displays(hud.console.main_mode)),
        drive=drive,
        console=with_playback_speed(hud.console, playback_speed),
    )


def paint_panel(
    painter,
    hud: ConsoleHud,
    *,
    hover: tuple[int, int] | None = None,
    row=None,
) -> Image.Image:
    console_rgba, console_size = painter.rgba(hud, hover=hover)
    console = Image.frombytes("RGBA", console_size, console_rgba)
    if row is None:
        return console
    panel = Image.new("RGBA", (console.width, console.height + row.shape[0]), (0, 0, 0, 0))
    panel.alpha_composite(console, (0, 0))
    panel.alpha_composite(Image.fromarray(row, "RGBA"), (0, console.height))
    return panel


class PanelPointer:
    def __init__(self, painter: ConsolePainter, *, post: Callable[[str], None]) -> None:
        self._painter = painter
        self._post = post
        self._size = (1, 1)
        self._tip: tuple[str, tuple[int, int]] | None = None

    def painted(self, size: tuple[int, int]) -> None:
        self._size = size

    def _pixel(self, u: float, v: float) -> tuple[int, int]:
        return surface_pixel(u, v, self._size)

    def press(self, u: float, v: float) -> None:
        self.release()
        px, py = self._pixel(u, v)
        left, top = hud_xy()
        command = self._painter.press_at(px + left, py + top)
        if command:
            self._post(command)

    def drag(self, u: float, v: float) -> None:
        if self._painter.holding:
            px, py = self._pixel(u, v)
            left, top = hud_xy()
            _, cy = self._pixel(u, v)
            command = self._painter.drag_to(px + left, cy + top)
            if command:
                self._post(command)

    def release(self) -> None:
        self._painter.release()

    def tooltip_anchor(self, uv: tuple[float, float] | None) -> tuple[int, int] | None:
        tip = ""
        if uv is not None and 0.0 <= uv[0] <= 1.0 and 0.0 <= uv[1] <= 1.0:
            px, py = self._pixel(*uv)
            tip = tooltip_at(self._painter.buttons, px, py)
        if not tip:
            self._tip = None
        elif self._tip is None or self._tip[0] != tip:
            self._tip = (tip, (px, py))
        return self._tip[1] if self._tip is not None else None
