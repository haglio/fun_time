from __future__ import annotations

import contextlib
import time
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, replace
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

TOPMOST_POLL_MS = 16


def opening_panel(status: str) -> LoadingPanel:
    shown = preview_marker.shown_as()
    return LoadingPanel(
        wordmark=preview_marker.APP_TITLE,
        status=status,
        ink=preview_marker.wordmark_ink(shown),
        icon=icon_image(preview_marker.icon_file(PROJECT_ICON, shown)),
    )


def _lay_a_topmost_floor_under_the_screen() -> None:
    create_hidden_topmost_window()


class Canvas(Protocol):
    def show(self, panel: LoadingPanel) -> None: ...

    def place_at(self, x: int, y: int) -> None: ...


class _LabelCanvas:
    def __init__(self, label: tk.Label) -> None:
        self._label = label

    def show(self, panel: LoadingPanel) -> None:
        self._label.image = ImageTk.PhotoImage(render(panel))
        self._label.configure(image=self._label.image)

    def place_at(self, x: int, y: int) -> None:
        self._label.place_configure(x=x, y=y)


@dataclass(frozen=True)
class OverlaySurface:
    root: tk.Tk
    canvas: Canvas
    title: str


class OverlayWindow:
    def __init__(
        self,
        progress_file: Path,
        surface: OverlaySurface,
        *,
        panel: LoadingPanel,
        stale_timeout_s: float,
        taking_over: TakingOver,
    ) -> None:
        self._progress_file = progress_file
        self._root = surface.root
        self._canvas = surface.canvas
        self._taking_over = taking_over
        self._panel = replace(panel, fraction=taking_over.carried(0.0))
        self._title = surface.title
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
        _lay_a_topmost_floor_under_the_screen()

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
            progress_file, OverlaySurface(root, canvas, title), panel=opening_panel(status),
            stale_timeout_s=stale_timeout_s, taking_over=taking_over or TakingOver(None),
        )
        canvas.show(window.panel)
        window._cover_every_monitor()

        window._have_esc_land_on_this_screen()

        root.after(POLL_MS, window._poll)
        root.after(TOPMOST_POLL_MS, window._stay_on_top)
        return window

    def _stay_on_top(self) -> None:
        if not self._hwnd:
            self._hwnd = find_window_by_title(self._title, exact=True)
        if self._hwnd:
            set_always_on_top(self._hwnd, True)
        with contextlib.suppress(tk.TclError):
            self._root.after(TOPMOST_POLL_MS, self._stay_on_top)

    def _have_esc_land_on_this_screen(self) -> None:
        self._root.bind("<Escape>", self._on_escape)
        self._root.focus_force()

    def _on_escape(self, _event: object = None) -> None:
        if not self._offering or self._status_held:
            return
        self._say_canceling()
        with contextlib.suppress(OSError):
            cancel_file_for(self._progress_file).write_text(f"{CANCEL_WORD}\n", encoding="utf-8")

    def _say_canceling(self) -> None:
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
            pass

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
        if not (progress := self._read_progress()).malformed and not progress.done:
            self._show(progress)
        self._root.update()
        self._taking_over.done(self._panel, covers_every_monitor=True)
        if on_shown is not None:
            on_shown()
        self._root.mainloop()
