"""What the main player draws on top of mpv's video, and in what order.

mpv owns the window and hardware-decodes the video; everything the main player shows is an
overlay bitmap on top of it, updated in place under a stable id.  Five things,
every painted frame: the timeline along the lower edge, the time readout at its
left, the console in the top-left corner, the volume chip above the timeline's
right-hand end, and the loop's two frames above their marks.

The order they are built in carries three rules that nothing outside this module
can see:

* The heatmap's color row is built at the INSET TRACK's width and framed at the
  WINDOW's, so the strip lines up with the plain bar underneath it.  One width
  for the row and another for the frame is not a slip.
* The room's two published files are read before anything drawn believes them,
  and the motion is read through the gate before the console panel is built out
  of it -- otherwise the pill and the line describe the frame before this one.
* The ids are the Z-ORDER, not the call order: the heatmap is under the loop's
  frames, which are under the console and the chip, which are all under the
  black :mod:`main_player.display` puts up when the room takes this slot away.  The
  thumbnails are drawn last and composite second from the lower edge.

Lived inline in ``main_player.app``'s run loop, where a frame could not be painted
without a window and libmpv, so none of the three had a test.
"""
from __future__ import annotations

from player_core.console_hud import ConsoleHud, hud_xy, with_playback_speed
from player_core.playhead import PlayheadHudPainter, readout_xy, video_playhead
from player_core.timeline import bar_track_x, progress_bar_bgra
from player_core.volume import VolumeHudPainter, chip_xy

from .overlay import heatmap_bgra, loop_thumbnail_xys, timeline_height

# Overlay ids (stable so each frame updates in place).
_OV_HEATMAP = 0
_OV_READOUT = 1
_OV_IN_THUMB = 4
_OV_OUT_THUMB = 5
_OV_CONSOLE = 6
_OV_VOLUME = 7
# Every one of the above: what a blanked display takes down with the video.
HUD_OVERLAYS = (_OV_HEATMAP, _OV_READOUT, _OV_IN_THUMB, _OV_OUT_THUMB, _OV_CONSOLE,
                _OV_VOLUME)


class ConsolePanel:
    """The top-left corner, and the four things it is built out of.

    The video's name and the dot saying whether a bare command lands here, what
    is selecting this playlist, what is driving the device, and every control
    Fun Time's dashboard used to hold for this slot.  The name heads it rather
    than sitting in a chip of its own beneath.

    Its own part, because it is where the frame's reading of the outside world
    happens: the room's two published files, and the gate that says how much of
    the motion to believe.  The other three overlays read none of that.
    """

    def __init__(self, session, *, room, drive_gate, console_hud, modes) -> None:
        self._session = session
        self._room = room
        self._drive_gate = drive_gate
        self._console_hud = console_hud
        self._modes = modes

    def bgra(self, *, hover):
        """This frame's panel.  Reads the room first, then asks the gate what of
        the motion it published this video's picture may believe -- drawn the
        other way round, the pill and the line describe the frame before this
        one."""
        self._room.refresh()
        drive = self._drive_gate.readout(
            self._room.drive,
            device_drives_itself=self._room.console.device_drives_itself,
        )
        return self._console_hud.bgra(ConsoleHud(
            modes=self._modes.hud,
            # The main player knows its own playback rate; Fun Time does not publish it, so
            # it is folded in here.  The dot's `active` and everything else came
            # down in the console file.
            console=with_playback_speed(self._room.console, self._session.speed),
            drive=drive,
        ), hover=hover)


class Painter:
    """The overlays of one frame, built from what the player is doing now."""

    def __init__(self, player, session, console: ConsolePanel, *, heatmap,
                 volume, loop_thumbs) -> None:
        self._player = player
        self._session = session
        self._console = console
        self._heatmap = heatmap
        self._volume = volume
        self._loop_thumbs = loop_thumbs
        # Its own, because nothing else draws the chip; built once rather than
        # per frame, because it keeps the bitmap until the level moves.
        self._volume_painter = VolumeHudPainter()
        self._readout_painter = PlayheadHudPainter()

    def paint(self, win_w: int, win_h: int, *, hover) -> None:
        """Put this frame's overlays up.  *hover* is where the pointer is, which
        is the one thing drawn here that the mouse owns rather than the player."""
        self._timeline(win_w, win_h)
        self._readout(win_w, win_h)
        self._panel(hover)
        self._chip(win_w, win_h)
        self._loop_frames(win_w, win_h)

    def _timeline(self, win_w: int, win_h: int) -> None:
        session = self._session
        # The heatmap fills the inset track, so build its color row at track
        # width; heatmap_bgra frames it full-width to line up with the plain bar.
        tx0, tx1 = bar_track_x(win_w)
        self._heatmap.update(
            session.current_video, session.current_funscript, session.duration_ms,
            tx1 - tx0,
            loop_state=session.loop_state,
            record_in_ms=session.record_in_ms,
            position_ms=session.position_ms,
        )
        if session.showing_picture:
            self._player.remove_overlay(_OV_HEATMAP)
            return
        hb = heatmap_bgra(self._heatmap, session.position_ms, session.loop_bounds, win_w)
        if hb is None:
            # Unscripted video: a plain clickable progress bar instead, still
            # showing the playcursor and any loop in/out marks.
            hb = progress_bar_bgra(
                session.position_ms, session.duration_ms, session.loop_bounds,
                win_w, record_in_ms=session.record_in_ms,
            )
        self._player.overlay(_OV_HEATMAP, 0, win_h - hb.shape[0], hb)

    def _readout(self, win_w: int, win_h: int) -> None:
        session = self._session
        hud = video_playhead(session.position_ms, session.duration_ms, self._player.frame_rate)
        if hud is None:
            self._player.remove_overlay(_OV_READOUT)
            return
        pill = self._readout_painter.bgra(hud)
        x, y = readout_xy(pill.shape[1], win_w=win_w, win_h=win_h,
                          timeline_h=timeline_height(self._heatmap))
        self._player.overlay(_OV_READOUT, x, y, pill)

    def _panel(self, hover) -> None:
        left, top = hud_xy()
        self._player.overlay(_OV_CONSOLE, left, top, self._console.bgra(hover=hover))

    def _chip(self, win_w: int, win_h: int) -> None:
        """The volume control, at the right-hand end of the row above the
        timeline — beside the transport, where a player's has always been."""
        vx, vy = chip_xy(win_w=win_w, win_h=win_h,
                         timeline_h=timeline_height(self._heatmap))
        self._player.overlay(_OV_VOLUME, vx, vy,
                             self._volume_painter.bgra(self._volume.hud))

    def _loop_frames(self, win_w: int, win_h: int) -> None:
        """Capture (on demand) and draw the loop's in and out frames above their
        timeline marks."""
        session, thumbs = self._session, self._loop_thumbs
        bounds = session.loop_bounds
        which = thumbs.needed(session.loop_state, bounds, session.position_ms)
        if which is not None:
            # Whatever mpv gives, including nothing: a capture is only ever
            # asked for while that side is empty, so handing back None leaves it
            # empty and the next frame asks again.
            thumbs.set(which, self._player.screenshot_bgra())
        if bounds is None:
            # By hand, because the ids are stable so each frame updates in
            # place: left alone, a canceled loop's frames stay over the video.
            self._player.remove_overlay(_OV_IN_THUMB)
            self._player.remove_overlay(_OV_OUT_THUMB)
            return
        in_at, out_at = loop_thumbnail_xys(
            self._heatmap, thumbs, bounds,
            track=bar_track_x(win_w), win_w=win_w, win_h=win_h)
        if in_at is not None:
            self._player.overlay(_OV_IN_THUMB, *in_at, thumbs.in_thumb)
        if out_at is not None:
            self._player.overlay(_OV_OUT_THUMB, *out_at, thumbs.out_thumb)
