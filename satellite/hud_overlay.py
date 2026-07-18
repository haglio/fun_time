"""Keep the lock HUD on screen and take its clicks.

Polls the panel fun_time publishes, re-renders it when it changes, and hands the
bitmap to mpv to composite into the video.  Presses and hover come in as window
coordinates from the run loop's pygame events and go out as fun_time commands
appended to the dashboard command file — the same channel the dashboard writes.

This is the whole of the HUD's runtime: there is no window to position, raise or
band, because the HUD is part of the frame.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from .hud import (
    MARGIN,
    HudClicks,
    HudModel,
    HudTargets,
    button_tooltip,
    hit_test_targets,
    parse_hud,
)
from .hud_paint import HudRenderer

logger = logging.getLogger(__name__)

# Overlay id, distinct from any other the satellite composites.
HUD_OVERLAY_ID = 10

_EMPTY_TARGETS = HudTargets(click=[], loop=[], label=[], expand=None)


def append_command(path: Path, line: str, *, attempts: int = 5, delay_s: float = 0.005) -> bool:
    """Append one command for fun_time's dispatch loop, retrying past its drain.

    The dispatch loop drains this file ~20x/s by renaming it, so a write that
    overlaps a drain hits a transient Windows sharing violation.  Retrying briefly
    turns that into a millisecond delay instead of a lost click; a persistently
    locked file drops the line (the next click lands) rather than raising into the
    run loop.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    for attempt in range(attempts):
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
            return True
        except OSError:
            if attempt < attempts - 1:
                time.sleep(delay_s)
    return False


class HudOverlay:
    """One satellite's lock HUD: published panel in, video overlay + commands out."""

    def __init__(
        self,
        *,
        hud_file: Path,
        command_file: Path,
        player,
        overlay_id: int = HUD_OVERLAY_ID,
        clock=time.monotonic,
    ) -> None:
        self._hud_file = Path(hud_file)
        self._command_file = Path(command_file)
        self._player = player
        self.overlay_id = overlay_id
        self._clock = clock
        # Which side this is (and so the panel's shape and the command prefix)
        # comes from the first published panel — the panel is the authority on
        # what it is, so the player needs no second way to be told.
        self._renderer: HudRenderer | None = None
        self._clicks: HudClicks | None = None
        self._published = ""          # the raw panel text last rendered
        self._model: HudModel | None = None
        self._hover_loop = ""
        self._hover_tip = ""
        self._hover_pos = (0, 0)
        self._shown = False
        self.targets: HudTargets = _EMPTY_TARGETS

    @property
    def active_loop(self) -> str:
        """Which axis the HUD shows as looping — optimistic after a click, then
        authoritative from the next published panel."""
        return self._clicks.active_loop if self._clicks is not None else ""

    def tick(self) -> None:
        """Re-read the published panel, redraw if it moved, and post a due click."""
        text = self._read()
        if text != self._published:
            self._published = text
            model = parse_hud(text) if text else None
            if model is not None:
                if self._renderer is None:
                    self._renderer = HudRenderer(model.side)
                    self._clicks = HudClicks(model.side)
                # The published panel is authoritative for the loop's lit state:
                # a clip auto-advancing inside a loop must not unlight it, and a
                # loop fun_time ended must not stay lit.
                self._clicks.active_loop = model.active_loop
            self._model = model
            self._draw()
        if self._clicks is not None:
            command = self._clicks.due(now=self._clock())
            if command:
                self._post(command)

    def press(self, x: int, y: int) -> None:
        """A left-click at window coordinates ``(x, y)``."""
        if self._clicks is None:
            return
        command = self._clicks.press(self.targets, *self._local(x, y), now=self._clock())
        if command:
            self._post(command)
            self._draw()  # a loop button lights up before fun_time answers

    def motion(self, x: int, y: int) -> None:
        """The cursor moved to window coordinates ``(x, y)``."""
        px, py = self._local(x, y)
        hover = hit_test_targets(self.targets.loop, px, py)
        tip = button_tooltip(self.targets.loop, self.targets.expand, px, py)
        if hover == self._hover_loop and tip == self._hover_tip:
            return
        self._hover_loop, self._hover_tip, self._hover_pos = hover, tip, (px, py)
        self._draw()

    def close(self) -> None:
        if self._shown:
            self._player.remove_overlay(self.overlay_id)
            self._shown = False

    def _local(self, x: int, y: int) -> tuple[int, int]:
        """Window coordinates as panel-local ones — the HUD sits at a fixed inset
        from the player window's top-left corner."""
        return x - MARGIN, y - MARGIN

    def _read(self) -> str:
        try:
            return self._hud_file.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _draw(self) -> None:
        if self._model is None or self._renderer is None:
            self.targets = _EMPTY_TARGETS
            self.close()
            return
        rendered = self._renderer.render(
            self._model, hover_loop=self._hover_loop,
            hover_tip=self._hover_tip, hover_pos=self._hover_pos,
        )
        self.targets = rendered.targets
        self._player.overlay(self.overlay_id, MARGIN, MARGIN, rendered.bgra)
        self._shown = True

    def _post(self, command: str) -> None:
        if not append_command(self._command_file, command):
            logger.warning("Dropped HUD command (command file locked): %s", command)
