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
from dataclasses import replace
from pathlib import Path

from player_core.drive_readout import DriveHud, read_drive
from player_core.file_channel import append_command
from player_core.hud_placement import HudCorner, HudEdge, hud_origin
from player_core.modes import Osr2State
from player_core.playhead import lower_edge_height
from player_core.satellite_hud import (
    MARGIN,
    HudClicks,
    HudModel,
    HudTargets,
    button_tooltip,
    hit_test_targets,
    parse_hud,
)
from player_core.satellite_hud_paint import HudRenderer
from player_core.timeline import TIMELINE_HEIGHT

logger = logging.getLogger(__name__)

# Overlay id, distinct from any other the satellite composites.
HUD_OVERLAY_ID = 10

_EMPTY_TARGETS = HudTargets(click=[], loop=[], filter=[], expand=None)


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
        drive_file: Path | None = None,
        drive_gate=None,
        over_the_video: bool = True,
    ) -> None:
        self._hud_file = Path(hud_file)
        self._drive_file = None if drive_file is None else Path(drive_file)
        self._drive_gate = drive_gate
        self._published_drive: DriveHud | None = None
        self._drive: DriveHud | None = None
        self._command_file = Path(command_file)
        self._player = player
        self.overlay_id = overlay_id
        self._clock = clock
        self._over_the_video = over_the_video
        # Which side this is (and so the panel's shape and the command prefix)
        # comes from the first published panel — the panel is the authority on
        # what it is, so the player needs no second way to be told.
        self._renderer: HudRenderer | None = None
        self._clicks: HudClicks | None = None
        self._published = ""          # the raw panel text last rendered
        self._model: HudModel | None = None
        self._video = ""              # the file the player last said it was on
        self._playback_speed: float | None = None
        self._hover_loop = ""
        self._hover_tip = ""
        self._hover_pos = (0, 0)
        self._shown = False
        self._window: tuple[int, int] | None = None
        self._origin = (MARGIN, MARGIN)
        self._panel_size: tuple[int, int] | None = None  # the slab's, while it shows
        self.targets: HudTargets = _EMPTY_TARGETS

    @property
    def edge(self) -> HudEdge:
        return self._model.hud_edge if self._model is not None else HudEdge.LOWER

    @property
    def active_loop(self) -> str:
        """Which axis the HUD shows as looping — optimistic after a click, then
        authoritative from the next published panel."""
        return self._clicks.active_loop if self._clicks is not None else ""

    def tick(self, video: str = "", playback_speed: float | None = None,
             window: tuple[int, int] | None = None) -> None:
        """Re-read the published panel, redraw if it or the clip on screen moved,
        and post a due click.

        *video* is the name of the file the player has open, which the HUD carries
        under its status line.  It is a redraw trigger in its own right: a satellite
        left to play walks its playlist by itself, and fun_time republishes the panel
        only when the map under it changes — so a name taken off the published panel
        alone would sit on a clip that had already rolled past.
        """
        text = self._read()
        redraw = (video != self._video or playback_speed != self._playback_speed
                  or window != self._window)
        self._video = video
        self._playback_speed = playback_speed
        self._window = window
        if text is not None and text != self._published:
            self._published = text
            model = parse_hud(text) if text else None
            if model is not None:
                if self._renderer is None:
                    self._renderer = HudRenderer(model.player)
                    self._clicks = HudClicks(model.player)
                # The published panel is authoritative for the loop's lit state:
                # a clip auto-advancing inside a loop must not unlight it, and a
                # loop fun_time ended must not stay lit.
                self._clicks.active_loop = model.active_loop
                self._clicks.active_filter = model.filter_query
            self._model = model
            redraw = True
        drive = self._motion()
        if drive != self._drive:
            self._drive = drive
            redraw = True
        if redraw:
            self._draw()
        if self._clicks is not None:
            command = self._clicks.due(now=self._clock())
            if command:
                self._post(command)

    def press(self, x: int, y: int) -> bool:
        """A left-click at window coordinates ``(x, y)``; whether the slab took it."""
        if self._clicks is None or not self._covers(x, y):
            return False
        command = self._clicks.press(self.targets, *self._local(x, y), now=self._clock())
        if command:
            self._post(command)
            self._draw()  # a loop button lights up before fun_time answers
        return True

    @property
    def holding(self) -> bool:
        return self._clicks is not None and self._clicks.holding

    def drag_to(self, x: int, y: int) -> str:
        command = self._clicks.drag_to(*self._local(x, y)) if self._clicks is not None else ""
        if command:
            self._post(command)
        return command

    def release(self) -> None:
        if self._clicks is not None:
            self._clicks.release()

    def motion(self, x: int, y: int) -> None:
        """The cursor moved to window coordinates ``(x, y)``."""
        px, py = self._local(x, y)
        hover = hit_test_targets(self.targets.loop, px, py)
        tip = button_tooltip(self.targets, px, py)
        if hover == self._hover_loop and tip == self._hover_tip:
            return
        self._hover_loop, self._hover_tip, self._hover_pos = hover, tip, (px, py)
        self._draw()

    def close(self) -> None:
        self._panel_size = None
        if self._shown:
            self._player.remove_overlay(self.overlay_id)
            self._shown = False

    def _covers(self, x: int, y: int) -> bool:
        if self._panel_size is None:
            return False
        left, top = self._origin
        width, height = self._panel_size
        return left <= x < left + width and top <= y < top + height

    def _local(self, x: int, y: int) -> tuple[int, int]:
        left, top = self._origin
        return x - left, y - top

    def _place(self, corner: HudCorner, size: tuple[int, int]) -> tuple[int, int]:
        if self._window is None:
            return MARGIN, MARGIN
        width, _height = self._window
        return hud_origin(corner, panel=size, window=self._window, margin=MARGIN,
                          lower_edge=lower_edge_height(width, timeline_h=TIMELINE_HEIGHT))

    def _read(self) -> str | None:
        """The published panel: its text, ``""`` when there is none to show, or
        None when this frame could not see it.

        Those last two are different answers and were once the same one.  The
        file is replaced by fun_time while this polls it every frame, so a read
        can lose that race and raise — and treating that as "no panel" takes the
        whole map off the video for a frame, then puts it back on the next one.
        Only the file actually being gone means there is nothing to draw.
        """
        try:
            return self._hud_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError:
            return None

    def _motion(self) -> DriveHud | None:
        if self._drive_file is None or self._model is None or not self._model.osr2:
            return None
        self._published_drive = read_drive(self._drive_file) or self._published_drive
        if self._drive_gate is None:
            return self._published_drive
        return self._drive_gate.readout(
            self._published_drive, device_drives_itself=self._model.osr2 == Osr2State.AUTO)

    def _draw(self) -> None:
        if self._model is None or self._renderer is None:
            self.targets = _EMPTY_TARGETS
            self.close()
            return
        corner = self._model.hud_corner if self._over_the_video else HudCorner.UPPER_LEFT
        rendered = self._renderer.render(
            replace(self._model, playback_speed=self._playback_speed, drive=self._drive,
                    drive_composed=self._drive_gate is not None, hud_corner=corner),
            video=self._video, hover_loop=self._hover_loop,
            hover_tip=self._hover_tip, hover_pos=self._hover_pos,
            may_grow_on_hover=self._over_the_video,
        )
        self.targets = rendered.targets
        height, width = rendered.bgra.shape[:2]
        self._panel_size = (width, height)
        self._origin = self._place(corner, self._panel_size)
        self._player.overlay(self.overlay_id, *self._origin, rendered.bgra)
        self._shown = True

    def _post(self, command: str) -> None:
        if not append_command(self._command_file, command):
            logger.warning("Dropped HUD command (command file locked): %s", command)
