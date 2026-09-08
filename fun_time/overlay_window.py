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
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING

from .cover_palette import (
    BG,
    FACE,
    HINT_DIM,
    TEXT_DIM,
    TROUGH,
    WORDMARK_MAGENTA,
)
from .monitors import MonitorInfo, virtual_desktop_rect
from .overlay_progress import parse_progress
from .project_paths import PROJECT_ICON
from .win32 import find_window_by_title, set_always_on_top

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


ICON_DISPLAY_SIZE = 128


def load_icon_image(ico_path: Path, size: int) -> PILImage | None:
    """An ICO as an RGBA PIL Image at *size*, or None without the file or
    Pillow."""
    try:
        from PIL import Image

        img = Image.open(ico_path)
        img = img.resize((size, size), Image.LANCZOS)  # largest, then downsample
        return img.convert("RGBA")
    except (ImportError, OSError):
        return None  # PIL's UnidentifiedImageError is an OSError


POLL_MS = 200

# How long another window may sit over the cover -- not POLL_MS, which is how
# stale the bar may be.  At 200ms it was a fifth of a second of a player through
# the scrim per raise.  Re-asserted through SetWindowPos on our own HWND, not
# Tk's ``-topmost``: WS_EX_TOPMOST is still set, so Tk may do nothing; only
# SetWindowPos re-inserts.
TOPMOST_POLL_MS = 16


@dataclass(frozen=True)
class CancelOption:
    """The Esc affordance a cover offers, and the words that go with it.
    Startup's carries one; shutdown's carries none — nothing is left to abort —
    so that cover never takes the focus either."""

    hint: str  # shown under the bar until the key is pressed

    pending: str
    """Held from the keypress on, so a step message still in flight cannot flip
    the line back to business as usual."""

    request: Callable[[], None]
    """Asks the orchestrator to stop."""

    requested: Callable[[], bool]
    """True once a cancel has been asked for by ANY route (:mod:`overlay_progress`
    has the two).  Without it, an Esc the hotkey hook caught left the cover
    reading "Press Esc to cancel" through the teardown it had started."""


@dataclass(frozen=True)
class _Content:  # the three widgets the cover writes to as it runs
    status_label: tk.Label
    progress_var: tk.DoubleVar
    hint_label: tk.Label


def _apply_theme(root: tk.Tk) -> None:
    """The two ttk styles the bar and its frame are drawn with."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # full style control; not every Tk ships it
    except tk.TclError:
        pass
    style.configure(
        "FunTime.Horizontal.TProgressbar",
        troughcolor=TROUGH,
        background=WORDMARK_MAGENTA,
        thickness=18,
        borderwidth=0,
    )
    style.configure("FunTime.TFrame", background=BG)


def _build_content(root: tk.Tk, *, origin: tuple[int, int], status: str,
                   hint: str) -> _Content:
    """The panel in the middle, centred on the main player's monitor rather
    than the virtual desktop's midpoint, which may fall between two."""
    frame = ttk.Frame(root, padding=24, style="FunTime.TFrame")
    origin_x, origin_y = origin
    frame.place(
        x=root.winfo_screenwidth() // 2 - origin_x,
        y=root.winfo_screenheight() // 2 - origin_y,
        anchor=tk.CENTER,
    )

    icon_img = load_icon_image(PROJECT_ICON, ICON_DISPLAY_SIZE)
    if icon_img is not None:
        try:
            from PIL import ImageTk

            icon_label = tk.Label(frame, bg=BG)
            # On the label: what keeps the PhotoImage from being collected.
            icon_label.image = ImageTk.PhotoImage(icon_img)
            icon_label.configure(image=icon_label.image)
            icon_label.pack(pady=(0, 12))
        except (ImportError, tk.TclError):
            pass  # no Tk extension, or a Tk that refuses it: come up plain

    tk.Label(frame, text="Fun Time", font=(FACE, 18, "bold italic"),
             fg=WORDMARK_MAGENTA, bg=BG).pack(pady=(0, 10))

    status_label = tk.Label(frame, text=status, font=(FACE, 10), fg=TEXT_DIM, bg=BG)
    status_label.pack(pady=(0, 10))

    progress_var = tk.DoubleVar(value=0)
    ttk.Progressbar(
        frame, variable=progress_var, maximum=100, length=360,
        mode="determinate", style="FunTime.Horizontal.TProgressbar",
    ).pack(pady=(0, 8))

    hint_label = tk.Label(frame, text=hint, font=(FACE, 8), fg=HINT_DIM, bg=BG)
    hint_label.pack()

    return _Content(status_label, progress_var, hint_label)


class OverlayWindow:
    """One borderless, always-on-top window covering the whole virtual desktop."""

    def __init__(
        self,
        progress_file: Path,
        *,
        title: str,
        status: str,
        stale_timeout_s: float,
        cancel: CancelOption | None = None,
        dismissable: bool = False,
        hint: str = "",
    ) -> None:
        self._progress_file = progress_file
        self._stale_timeout_s = stale_timeout_s
        self._cancel = cancel
        self._last_modified = 0.0
        self._status_held = False
        self._title = title
        self._hwnd = 0

        self._root = tk.Tk()
        self._root.title(title)
        self._root.resizable(False, False)
        self._root.attributes("-topmost", True)
        self._root.overrideredirect(True)
        self._root.configure(bg=BG)

        # Out here, not in the failure path, where a Tk not answering raised
        # again with nothing left to catch it.
        desktop = virtual_desktop_rect()
        if desktop is None:
            desktop = MonitorInfo(
                x=0, y=0,
                width=self._root.winfo_screenwidth(),
                height=self._root.winfo_screenheight(),
            )
        vx, vy = desktop.x, desktop.y

        self._root.geometry(f"{desktop.width}x{desktop.height}+{vx}+{vy}")

        _apply_theme(self._root)

        self._content = _build_content(
            self._root, origin=(vx, vy), status=status,
            hint=cancel.hint if cancel else hint,
        )

        if cancel is not None:
            # The focus is taken so Esc lands here rather than on whatever the
            # session put up last.  Not the route the cancel rests on, though;
            # see CancelOption.requested.
            self._root.bind("<Escape>", self._on_escape)
            self._root.focus_force()
        elif dismissable:  # one only somebody else can remove traps the monitors
            self._root.bind("<Escape>", lambda _e: self._root.destroy())
            self._root.focus_force()

        self._root.after(POLL_MS, self._poll)
        self._root.after(TOPMOST_POLL_MS, self._stay_on_top)

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
        if self._cancel is None or self._status_held:
            return
        self._hold_status()
        try:
            self._cancel.request()
        except OSError:
            pass

    def _hold_status(self) -> None:
        """Say we are cancelling, and go on saying it: a step message still in
        flight would otherwise flip the line back while the teardown runs."""
        if self._cancel is None:
            return
        self._status_held = True
        try:
            self._content.status_label.configure(text=self._cancel.pending)
            self._content.hint_label.configure(text="")
        except tk.TclError:
            pass

    def _poll(self) -> None:
        # A cancel the hotkey script asked for on our behalf: the flag is on
        # disk and no key ever reached this window.
        if self._cancel is not None and not self._status_held:
            try:
                if self._cancel.requested():
                    self._hold_status()
            except OSError:
                pass
        try:
            if self._progress_file.exists():
                mtime = self._progress_file.stat().st_mtime
                progress = parse_progress(
                    self._progress_file.read_text(encoding="utf-8"))

                if progress.done:
                    self._root.destroy()
                    return

                # A torn write is not a step: hold the last readable line.
                if not progress.malformed:
                    if progress.total > 0:
                        self._content.progress_var.set(
                            progress.step / progress.total * 100)
                    if progress.message and not self._status_held:
                        self._content.status_label.configure(text=progress.message)

                self._last_modified = mtime

            # Unmoved for stale_timeout_s: the orchestrator died holding the
            # cover up.  Never leave the desktop under a panel that will not go.
            if self._last_modified > 0:
                age = time.time() - self._last_modified
                if age > self._stale_timeout_s:
                    self._root.destroy()
                    return

        except (OSError, tk.TclError):
            pass

        try:
            self._root.after(POLL_MS, self._poll)
        except tk.TclError:
            pass  # window already destroyed

    def run(self, on_shown: Callable[[], None] | None = None) -> None:
        """Show the cover and hold it until the progress file says otherwise.
        ``update()`` returns only once Tk has created, shown and first painted
        the window, so *on_shown* fires when the cover is genuinely on screen --
        a signal teardown can hold its first kill on."""
        self._root.update()
        if on_shown is not None:
            on_shown()
        self._root.mainloop()
