"""The cover Fun Time puts over every monitor while its windows are changing.

A session's windows arrive one at a time and leave the same way, so both ends
raise a cover and work under it.  Borderless and always on top; it closes on
:mod:`overlay_progress`'s DONE — never on a full bar, which comes seconds
earlier while the room is still being put in z-order.
"""
from __future__ import annotations

import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from app_support.loading_screen import Placement, Screens, over_every_monitor, screens
from PIL import ImageTk
from shared_ui.loading_handover import TakingOver
from shared_ui.loading_panel import LoadingPanel, icon_image, render, size
from shared_ui.palette import LOADING_GROUND, as_hex

from . import preview_marker
from .overlay_progress import (
    CANCEL_WORD,
    CANCELING,
    Progress,
    cancel_file_for,
    parse_progress,
    what_the_flag_asks,
)
from .project_paths import PROJECT_ICON
from .win32 import create_hidden_topmost_window, find_window_by_title, set_always_on_top

POLL_MS = 200

# How long another window may sit over the cover -- not POLL_MS, which is how
# stale the bar may be.  At 200ms it was a fifth of a second of a Funestra through
# the scrim per raise.  Re-asserted through SetWindowPos on our own HWND, not
# Tk's ``-topmost``: WS_EX_TOPMOST is still set, so Tk may do nothing; only
# SetWindowPos re-inserts.
TOPMOST_POLL_MS = 16


def opening_panel(status: str) -> LoadingPanel:
    """The panel as the cover first shows it: this app's icon and name, in the
    tone the dashboard writes the name in, over the launch's first words."""
    shown = preview_marker.shown_as()
    return LoadingPanel(
        wordmark=preview_marker.APP_TITLE,
        status=status,
        ink=preview_marker.wordmark_ink(shown),
        icon=icon_image(preview_marker.icon_file(PROJECT_ICON, shown)),
    )


class Canvas(Protocol):
    """Where the panel is drawn: the one Tk label in the middle of the cover."""

    def show(self, panel: LoadingPanel) -> None: ...

    def place_at(self, x: int, y: int) -> None: ...


class _LabelCanvas:
    def __init__(self, label: tk.Label) -> None:
        self._label = label

    def show(self, panel: LoadingPanel) -> None:
        # On the label: what keeps the PhotoImage from being collected.
        self._label.image = ImageTk.PhotoImage(render(panel))
        self._label.configure(image=self._label.image)

    def place_at(self, x: int, y: int) -> None:
        self._label.place_configure(x=x, y=y)


class OverlayWindow:
    """One borderless, always-on-top window covering the whole virtual desktop."""

    def __init__(
        self,
        progress_file: Path,
        root: tk.Tk,
        canvas: Canvas,
        *,
        panel: LoadingPanel,
        title: str,
        stale_timeout_s: float,
        taking_over: TakingOver,
    ) -> None:
        self._progress_file = progress_file
        self._root = root
        self._canvas = canvas
        self._taking_over = taking_over
        self._panel = replace(panel, fraction=taking_over.carried(0.0))
        self._title = title
        self._stale_timeout_s = stale_timeout_s
        self._unmoved_since = time.time()
        self._status_held = False
        self._offering = False
        self._hwnd = 0
        self._placement: Placement | None = None

    @property
    def panel(self) -> LoadingPanel:
        return self._panel

    @classmethod
    def over_every_monitor(
        cls, progress_file: Path, *, title: str, status: str, stale_timeout_s: float,
        taking_over: TakingOver | None = None,
    ) -> OverlayWindow:
        # Made first, so the cover goes up over it and it stays under the cover
        # for good: a window put under the cover joins the topmost band only if
        # some topmost window sits below the cover.
        create_hidden_topmost_window()

        ground = as_hex(LOADING_GROUND)
        root = tk.Tk()
        root.title(title)
        root.resizable(False, False)
        root.attributes("-topmost", True)
        root.overrideredirect(True)
        root.configure(bg=ground)

        label = tk.Label(root, bg=ground, borderwidth=0, highlightthickness=0)
        label.place(x=0, y=0)
        canvas = _LabelCanvas(label)

        window = cls(
            progress_file, root, canvas, panel=opening_panel(status), title=title,
            stale_timeout_s=stale_timeout_s, taking_over=taking_over or TakingOver(None),
        )
        canvas.show(window.panel)
        window._cover_every_monitor()

        # The focus is taken so Esc lands here rather than on whatever the
        # session put up last, before the hotkey script is up to take it.
        root.bind("<Escape>", window._on_escape)
        root.focus_force()

        root.after(POLL_MS, window._poll)
        root.after(TOPMOST_POLL_MS, window._stay_on_top)
        return window

    def _stay_on_top(self) -> None:
        """Take the top of the topmost band back, and keep taking it: every
        window a session raises lands above this one.  Our own window, so the
        call skips the stalled-window guard.  The handle is looked up by title,
        once -- ``winfo_id`` is not reliably the top-level HWND."""
        if not self._hwnd:
            self._hwnd = find_window_by_title(self._title, exact=True)
        if self._hwnd:
            set_always_on_top(self._hwnd, True)
        try:
            self._root.after(TOPMOST_POLL_MS, self._stay_on_top)
        except tk.TclError:
            pass  # window already destroyed

    def _on_escape(self, _event: object = None) -> None:
        if not self._offering or self._status_held:
            return
        self._say_canceling()
        try:
            cancel_file_for(self._progress_file).write_text(f"{CANCEL_WORD}\n", encoding="utf-8")
        except OSError:
            pass

    def _say_canceling(self) -> None:
        """And go on saying it: a phase still in flight would otherwise flip the
        line back while the teardown runs."""
        self._status_held = True
        self._redraw(status=CANCELING, hint="")

    def _cover_every_monitor(self) -> None:
        placement = over_every_monitor(size(), screens() or self._screens_as_tk_sees_them())
        if placement == self._placement:
            return
        self._placement = placement
        x, y, width, height = placement.window
        self._root.geometry(f"{width}x{height}+{x}+{y}")
        self._canvas.place_at(*placement.panel)

    def _screens_as_tk_sees_them(self) -> Screens:
        whole = (self._root.winfo_screenwidth(), self._root.winfo_screenheight())
        return Screens(virtual=(0, 0, *whole), primary=whole, pointer_work_area=(0, 0, *whole))

    def _poll(self) -> None:
        self._cover_every_monitor()
        progress = self._read_progress()
        try:
            if progress.done or self._unmoved_too_long():
                self._root.destroy()
                return
            if not progress.malformed:
                self._show(progress)
            self._root.after(POLL_MS, self._poll)
        except tk.TclError:
            pass  # window already destroyed

    def _read_progress(self) -> Progress:
        try:
            moved_at = self._progress_file.stat().st_mtime
            progress = parse_progress(self._progress_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Progress(malformed=True)
        self._unmoved_since = moved_at
        return progress

    def _unmoved_too_long(self) -> bool:
        return time.time() - self._unmoved_since > self._stale_timeout_s

    def _show(self, progress: Progress) -> None:
        self._offering = bool(progress.hint)
        if progress.total > 0:
            self._redraw(fraction=self._taking_over.carried(progress.step / progress.total))
        if progress.hint and what_the_flag_asks(
                cancel_file_for(self._progress_file)) == CANCEL_WORD:
            self._say_canceling()
        if not self._status_held:
            self._redraw(status=progress.message or self._panel.status, hint=progress.hint)

    def _redraw(self, **changes) -> None:
        panel = replace(self._panel, **changes)
        if panel != self._panel:
            self._panel = panel
            self._canvas.show(panel)

    def run(self, on_shown: Callable[[], None] | None = None) -> None:
        """Show the cover and hold it until the progress file says otherwise.
        ``update()`` returns only once Tk has created, shown and first painted
        the window, so *on_shown* fires when the cover is genuinely on screen --
        a signal teardown can hold its first kill on."""
        if not (progress := self._read_progress()).malformed and not progress.done:
            self._show(progress)
        self._root.update()
        self._taking_over.done(self._panel, covers_every_monitor=True)
        if on_shown is not None:
            on_shown()
        self._root.mainloop()
