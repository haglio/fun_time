"""The main console, hung in the headset -- what to put on it, and where.

The desktop paints it onto the main player's window, scrubber and volume chip
under the video.  Baked into an immersive video it would warp with it, down
at the nadir, so here it is a small screen of its own, high in the forward
band -- and read-only, the scene having no pointer.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
from PIL import Image
from player_core.console_hud import ConsoleHud, ModeHud
from player_core.timeline import TIMELINE_HEIGHT, progress_bar_bgra
from player_core.volume import VolumeHud, chip_xy

from fun_time.mode_plan import nau_displays

# Straight ahead, above the primary's top edge (a 16:9 primary spanning 72° is
# about 40° tall): out of the action, which sits low in an immersive picture.
PANEL_AZIMUTH_DEG = 0.0
PANEL_WIDTH_DEG = 24.0
PANEL_ELEVATION_DEG = 32.0

_ROW_GAP = 6


def panel_hud(
    engine_hud: ConsoleHud | None,
    *,
    video_title: str,
    clip_title: str,
    loading: str | None,
) -> ConsoleHud:
    """The engine's console with its top line re-said for the mode: the video's
    name under a video, the clip's (or the one still decoding) in genau mode.
    With no engine console (the broker has the room) the panel still names
    what is playing."""
    hud = engine_hud if engine_hud is not None else ConsoleHud()
    title = video_title if nau_displays(hud.console.mode) else (loading or clip_title)
    return replace(hud, modes=ModeHud(video=title))


def _rgba(bgra: np.ndarray) -> Image.Image:
    return Image.fromarray(np.ascontiguousarray(bgra[:, :, [2, 1, 0, 3]]), "RGBA")


def paint_panel(
    painter,
    hud: ConsoleHud,
    *,
    scrubber: tuple[float, float] | None,
    chip: VolumeHud,
    chip_painter,
) -> Image.Image:
    """The console with the furniture row under it: the scrubber, given
    ``(position_ms, duration_ms)`` (None for a clip, which loops), and the chip
    at its right end where every desktop player puts it."""
    console_rgba, console_size = painter.rgba(hud)
    console = Image.frombytes("RGBA", console_size, console_rgba)
    chip_image = _rgba(chip_painter.bgra(chip))
    width = console.width
    timeline_h = TIMELINE_HEIGHT if scrubber is not None else 0
    row_h = max(timeline_h, chip_image.height)
    height = console.height + _ROW_GAP + row_h
    panel = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    panel.alpha_composite(console, (0, 0))
    row_top = console.height + _ROW_GAP
    if scrubber is not None:
        position_ms, duration_ms = scrubber
        bar = _rgba(progress_bar_bgra(position_ms, duration_ms, None, width))
        panel.alpha_composite(bar, (0, height - bar.height))
    x, y = chip_xy(win_w=width, win_h=height, timeline_h=timeline_h)
    panel.alpha_composite(chip_image, (max(0, x), max(row_top, y)))
    return panel
