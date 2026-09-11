"""What the mouse does to the main player's window.

Four things are under the pointer, and they are asked in this order because each
floats over the one under it: the console's own buttons at the top left, the
volume chip at the right-hand end of the timeline row, the rest of that row, and
the video everywhere else.

A press that a console button takes never reaches the video; a press on the chip
is never also a press on what is under it.  A drag is different again — the
console's bands keep a drag that wanders off them, and the volume slider takes
one only along its own track — so a held pointer is offered to whoever grabbed
it rather than to whatever it happens to be over.

Lived as a closure and a chain of event branches inside ``main_player.app``'s run loop,
so the one thing it decides — whether a press seeks or pauses — could only be
exercised by opening a window and clicking in it.  Inverting the test, so a click
on the video seeked and a click on the timeline paused, left the whole suite
green.
"""
from __future__ import annotations

from player_core.timeline import bar_track_x

from .overlay import timeline_height

# Fun Time's verb for the whole room's pause, spelled here as every verb this
# player asks for is: the room has the pause, not this player.  In a session
# The main player's own paused state is the room's flag file, re-read every pass, so a
# player that paused itself would be playing again a frame later.
OMNIPAUSE_TOGGLE = "omnipause_toggle"


class Pointer:
    """The window under the pointer, and what a press or a drag on it does."""

    def __init__(self, session, heatmap, volume, console_hud, dashboard) -> None:
        self._session = session
        self._heatmap = heatmap
        self._volume = volume
        self._console_hud = console_hud
        self._dashboard = dashboard
        self._hover: tuple[int, int] | None = None

    @property
    def hover(self) -> tuple[int, int] | None:
        """Where to name the console button under the pointer, else None."""
        return self._hover

    def press(self, mx: int, my: int, *, win_w: int, win_h: int) -> None:
        """Take a press at window ``(mx, my)``."""
        asked = self._console_hud.press_at(mx, my)
        if asked:
            self._dashboard.post(asked)
            return
        row_h = timeline_height(self._heatmap)
        if self._volume.press_at(mx, my, win_w=win_w, win_h=win_h, timeline_h=row_h):
            return
        if my >= win_h - row_h:
            self._session.seek_to(self._time_at(mx, win_w))
        else:
            self._dashboard.post(OMNIPAUSE_TOGGLE)

    def release(self) -> None:
        """Let go of whatever a press took hold of."""
        self._console_hud.release()

    def motion(self, mx: int, my: int, *, held: bool,
               win_w: int, win_h: int) -> None:
        """Follow the pointer to window ``(mx, my)``, with the button *held* or not.

        A pointer that is not held is only ever naming a button.  A held one
        belongs to whichever control took hold of it: a band on the drive
        readout keeps the drag even as the pointer wanders off it, and says
        nothing while the level under it has not moved.  Held over nothing, the
        volume slider gets its turn, and a drag that began elsewhere misses the
        chip and does nothing.
        """
        self._hover = self._console_hud.hover_at(mx, my)
        if not held:
            # The button came up somewhere this loop never saw it — over another
            # window, or off the screen — so nothing is held.
            self._console_hud.release()
        elif self._console_hud.holding:
            dragged = self._console_hud.drag_to(mx, my)
            if dragged:
                self._dashboard.post(dragged)
        else:
            self._volume.drag_at(mx, my, win_w=win_w, win_h=win_h,
                                 timeline_h=timeline_height(self._heatmap))

    def _time_at(self, mx: int, win_w: int) -> float:
        """The media time the timeline puts under *mx*.

        The strip maps whatever window it is currently showing — the whole video
        normally, and while a loop is being recorded the zoomed section around
        the in point — so the press and the picture agree.  Before the first
        strip is built there is no window yet and the video's own length is the
        map instead.

        Both the heatmap strip and the plain bar are inset to the same track, so
        a press maps onto it the same way; past either end it saturates, which
        is what a pointer dragged off the end is asking for.
        """
        start_ms, end_ms = self._heatmap.window
        if end_ms <= start_ms:
            end_ms = start_ms + self._session.duration_ms
        x0, x1 = bar_track_x(win_w)
        frac = min(1.0, max(0.0, (mx - x0) / max(1, x1 - x0)))
        return start_ms + frac * (end_ms - start_ms)
