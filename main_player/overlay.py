"""Where the main player's own overlays go, and what the heatmap strip is made of.

The pure decision logic (the visible time window, strip geometry) lives here
untied to pygame so it is unit-testable; the drawing helpers further down turn
those decisions into textures.
"""
from __future__ import annotations

from player_core.modes import LoopState
from player_core.timeline import TIMELINE_HEIGHT, bar_track_x, bar_x, progress_bar_bgra

from .heatmap import build_heatmap

_ZOOM_SPAN_START_MS = 20_000.0
_ZOOM_LEAD_FRAC = 0.10
_ZOOM_GROW_FRAC = 0.85


class ZoomWindow:
    """Stepped time window for the recording zoom.

    Shows [in - lead, in + span] with a 10% lead so the in point sits just
    inside the left edge; span starts at 20s and doubles whenever the
    playhead passes 85% of the window, so the view rescales in steps
    rather than sliding continuously.
    """

    def __init__(self, *, in_ms: float) -> None:
        self._in_ms = in_ms
        self._span = _ZOOM_SPAN_START_MS

    @property
    def in_ms(self) -> float:
        return self._in_ms

    @property
    def bounds(self) -> tuple[float, float]:
        return self._in_ms - self._span * _ZOOM_LEAD_FRAC, self._in_ms + self._span

    def update(self, position_ms: float) -> None:
        while position_ms > self._grow_at():
            self._span *= 2

    def _grow_at(self) -> float:
        start, end = self.bounds
        return start + (end - start) * _ZOOM_GROW_FRAC


_IDLE_HEIGHT = 24
_RECORDING_HEIGHT = 48


class HeatmapStrip:
    """Funscript heatmap pinned to the window's lower edge.

    Normally a full-duration view; while a loop is being recorded it
    becomes a taller strip zoomed into the section around the in point
    (a stepped ZoomWindow), so the user can judge where to end the loop.
    The color row is expensive to build (one bucket per pixel), so
    update() rebuilds only when the video, the width, or the visible
    time window changes.
    """

    def __init__(self) -> None:
        self._key: tuple | None = None
        self._colors: list[tuple[int, int, int]] = []
        self._duration_ms = 0.0
        self._zoom: ZoomWindow | None = None

    @property
    def colors(self) -> list[tuple[int, int, int]]:
        return self._colors

    @property
    def height(self) -> int:
        """Strip height in px — 0 when there is nothing to draw."""
        if not self._colors:
            return 0
        return _RECORDING_HEIGHT if self._zoom is not None else _IDLE_HEIGHT

    @property
    def window(self) -> tuple[float, float]:
        """Visible time range (start_ms, end_ms) the strip currently maps."""
        if self._zoom is not None:
            return self._zoom.bounds
        return 0.0, self._duration_ms

    @property
    def record_in_ms(self) -> float | None:
        """In point of the recording being zoomed — None outside recording."""
        return self._zoom.in_ms if self._zoom is not None else None

    def update(
        self,
        video_key,
        funscript,
        duration_ms: float,
        width: int,
        *,
        loop_state: LoopState = LoopState.NORMAL,
        record_in_ms: float | None = None,
        position_ms: float = 0.0,
    ) -> None:
        if loop_state is LoopState.RECORDING and funscript is not None:
            if self._zoom is None:
                self._zoom = ZoomWindow(in_ms=record_in_ms)
            self._zoom.update(position_ms)
        else:
            self._zoom = None
        self._duration_ms = duration_ms
        key = (video_key, width, self.window)
        if key == self._key:
            return
        self._key = key
        if funscript is None:
            self._colors = []
        else:
            track_x0, track_x1 = bar_track_x(width)
            start, end = self.window
            self._colors = build_heatmap(
                funscript, track_x1 - track_x0, start_ms=start, end_ms=end,
            )




def timeline_height(heatmap: HeatmapStrip) -> int:
    """How tall the timeline row is under the current video.

    The heatmap strip when there is a funscript — taller while a loop is being
    recorded — and the plain progress bar's own height when there is not, since
    every video has a clickable timeline and a row of no height would put it
    outside the window.  Asked by everything measured against the lower edge of the
    window: where a press lands, and where the volume chip sits.
    """
    return heatmap.height or TIMELINE_HEIGHT


_OUT_CAPTURE_LEAD_MS = 400.0


class LoopThumbCapture:
    """Decides when to grab the loop's in/out frame thumbnails.

    The frames come from mpv screenshots (the caller does the actual grab),
    so this only owns the *when*: the in frame once the loop starts, the out
    frame once playback nears the out point.  Cleared when the loop changes
    or ends.  ``in_thumb`` / ``out_thumb`` hold the grabbed BGRA arrays.
    """

    def __init__(self) -> None:
        self._bounds: tuple[int, int] | None = None
        self.in_thumb = None
        self.out_thumb = None

    def needed(self, loop_state: LoopState, loop_bounds, position_ms: float) -> str | None:
        if loop_state is not LoopState.LOOPING or loop_bounds is None:
            self._bounds = None
            self.in_thumb = None
            self.out_thumb = None
            return None
        if loop_bounds != self._bounds:
            self._bounds = loop_bounds
            self.in_thumb = None
            self.out_thumb = None
        if self.in_thumb is None:
            return "in"
        if self.out_thumb is None and position_ms >= loop_bounds[1] - _OUT_CAPTURE_LEAD_MS:
            return "out"
        return None

    def set(self, which: str, thumb) -> None:
        if which == "in":
            self.in_thumb = thumb
        elif which == "out":
            self.out_thumb = thumb


def label_xs(in_x: int, out_x: int, in_w: int, out_w: int, win_w: int) -> tuple[int, int]:
    """Left edges for the in/out thumbnail labels: centered on their markers,
    clamped on-screen, and the out label nudged right of the in label when
    they would overlap."""
    ix = max(0, min(win_w - in_w, in_x - in_w // 2))
    ox = max(0, min(win_w - out_w, out_x - out_w // 2))
    if ox < ix + in_w:
        ox = min(win_w - out_w, ix + in_w + 2)
    return ix, ox


def time_to_x(ms: float, start_ms: float, end_ms: float, width: int) -> int:
    """Strip x for a timestamp: its fraction of [start_ms, end_ms], kept on-strip."""
    span = end_ms - start_ms
    if span <= 0:
        return 0
    return max(0, min(width - 1, int((ms - start_ms) / span * width)))


def loop_thumbnail_xys(
    heatmap: HeatmapStrip,
    thumbs: LoopThumbCapture,
    bounds: tuple[int, int],
    *,
    track: tuple[int, int],
    win_w: int,
    win_h: int,
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """Where the loop's in and out frames go: ``(in_xy, out_xy)``, either None
    while that frame has not been grabbed.

    Each sits above its own mark on the inset *track* — centered on it, kept
    on-screen, and stepped apart when the two marks are close enough that the
    frames would overlap — and clear of the timeline row itself, whose height
    is the strip's where there is a funscript and the plain bar's where there
    is not.  Pure geometry: the grabbing and the drawing are the caller's, and
    what it is told here is only where to put them.
    """
    start_ms, end_ms = heatmap.window
    tx0, tx1 = track
    track_w = tx1 - tx0
    in_x = tx0 + time_to_x(bounds[0], start_ms, end_ms, track_w)
    out_x = tx0 + time_to_x(bounds[1], start_ms, end_ms, track_w)
    in_t, out_t = thumbs.in_thumb, thumbs.out_thumb
    ix, ox = label_xs(
        in_x, out_x,
        in_t.shape[1] if in_t is not None else 1,
        out_t.shape[1] if out_t is not None else 1,
        win_w,
    )
    above = win_h - timeline_height(heatmap) - 2
    return (
        (ix, above - in_t.shape[0]) if in_t is not None else None,
        (ox, above - out_t.shape[0]) if out_t is not None else None,
    )


# --- RGBA overlay rendering (BGRA arrays for mpv overlay_add) -----------------
# mpv owns the window and hardware-decodes the video; the main player's overlays go on top
# as BGRA bitmaps.  No pygame — these produce plain numpy arrays.


def timeline_x(heatmap, ms, width):
    start_ms, end_ms = heatmap.window
    return bar_x(ms - start_ms, end_ms - start_ms, *bar_track_x(width))


def timeline_bgra(heatmap, position_ms, loop_bounds, width, *, record_in_ms=None):
    start_ms, end_ms = heatmap.window
    return progress_bar_bgra(
        position_ms - start_ms, end_ms - start_ms,
        None if loop_bounds is None else (loop_bounds[0] - start_ms, loop_bounds[1] - start_ms),
        width,
        record_in_ms=None if record_in_ms is None else record_in_ms - start_ms,
        height=timeline_height(heatmap), heatmap=heatmap.colors,
    )
