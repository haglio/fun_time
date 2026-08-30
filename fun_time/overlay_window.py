"""The cover Fun Time puts over every monitor while its windows are changing.

A session's windows arrive one at a time and leave the same way, so both ends
of one raise a cover and do the work behind it.  The window is borderless and
always on top; it reads how far the work has got from a progress file the
orchestrator writes, and closes itself when that file says DONE — and ONLY
then, never on a full bar, which comes seconds earlier while the room is still
being put in z-order.

Startup's cover offers a way out and shutdown's does not — see ``CancelOption``
for that difference and for the reason it is the only one.
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
    WORDMARK_PINK,
)
from .monitors import MonitorInfo, virtual_desktop_rect
from .overlay_progress import parse_progress
from .project_paths import PROJECT_ICON
from .win32 import find_window_by_title, set_always_on_top

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


ICON_DISPLAY_SIZE = 128


def load_icon_image(ico_path: Path, size: int) -> PILImage | None:
    """Load an ICO file and return an RGBA PIL Image resized to *size* x *size*.

    Returns ``None`` if the file is missing or Pillow is unavailable.
    """
    try:
        from PIL import Image

        img = Image.open(ico_path)
        # Pick the largest available icon (256x256 in our ICO) then
        # high-quality downsample to the requested display size.
        img = img.resize((size, size), Image.LANCZOS)
        return img.convert("RGBA")
    except (ImportError, OSError):
        # No Pillow, no such file, or a file that is not an image (PIL's
        # UnidentifiedImageError is an OSError).
        return None


POLL_MS = 200

# How often the cover takes the top of the topmost band back (see
# _stay_on_top).  Separate from POLL_MS, and much shorter, because they answer
# different questions: how stale the bar may be, versus how long another window
# may sit over the cover.  This number IS that second answer — at 200ms it was
# a fifth of a second of a player through the scrim per raise, plainly visible;
# a frame of it is not.
#
# Re-asserted through SetWindowPos on our own HWND rather than Tk's
# ``-topmost``: the style is still set (being pushed down within the band does
# not clear WS_EX_TOPMOST), so Tk has nothing to change and may do nothing at
# all.  What is needed is the re-insertion, which only SetWindowPos gives.
TOPMOST_POLL_MS = 16


@dataclass(frozen=True)
class CancelOption:
    """The Esc affordance an overlay offers, and the words that go with it.

    Startup's cover carries one: a session that is still assembling can still be
    called off.  Shutdown's carries none — by the time the windows are going
    away there is nothing left to abort — so that overlay also never takes the
    keyboard focus, and the last thing the user typed at goes on owning it.
    """

    hint: str
    """Shown under the bar until the key is pressed."""

    pending: str
    """The status line holds this from the keypress onward, so a step message
    still in flight cannot flip it back to business as usual."""

    request: Callable[[], None]
    """Asks the orchestrator to stop.  The cover stays up until the orchestrator
    answers, so nothing half-built is ever revealed."""

    requested: Callable[[], bool]
    """True once a cancel has been asked for by any route.  Esc reaches the
    orchestrator two ways — this window's own binding, and the hotkey script's
    global hook, which is the one that still works when something else has taken
    the focus — so the words below follow the request rather than the keypress.
    Without this, an Esc the hook caught left the cover reading "Press Esc to
    cancel" right through the teardown it had just started."""


@dataclass(frozen=True)
class _Content:
    """The three widgets the cover writes to as it runs."""

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
        background=WORDMARK_PINK,
        thickness=18,
        borderwidth=0,
    )
    style.configure("FunTime.TFrame", background=BG)


def _build_content(root: tk.Tk, *, origin: tuple[int, int], status: str,
                   hint: str) -> _Content:
    """The panel in the middle: icon, wordmark, status, bar, hint.

    Centred on the main player's monitor, not on the virtual desktop's
    midpoint, which may fall between two of them.
    """
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
            # On the label, which is what keeps the PhotoImage from being
            # collected out from under the icon it is drawing.
            icon_label.image = ImageTk.PhotoImage(icon_img)
            icon_label.configure(image=icon_label.image)
            icon_label.pack(pady=(0, 12))
        except (ImportError, tk.TclError):
            # Pillow without its Tk extension, or a Tk that will not take the
            # image: the cover comes up plain rather than not at all.
            pass

    tk.Label(frame, text="Fun Time", font=(FACE, 18, "bold italic"),
             fg=WORDMARK_PINK, bg=BG).pack(pady=(0, 10))

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

        # The fallback is asked for OUT here, not inside the failure path where
        # a Tk not answering either raised again with nothing left to catch it.
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
            hint=cancel.hint if cancel else "",
        )

        if cancel is not None:
            # Esc anywhere on the overlay asks the orchestrator to stop.  The
            # focus is taken so the key lands here rather than on whatever the
            # session put up last.  It is not the only route — the hotkey script
            # hooks the same key and needs no focus at all — so this is the
            # binding that works when the cover has the focus, not the one the
            # cancel rests on.
            self._root.bind("<Escape>", self._on_escape)
            self._root.focus_force()

        self._root.after(POLL_MS, self._poll)
        self._root.after(TOPMOST_POLL_MS, self._stay_on_top)

    def _stay_on_top(self) -> None:
        """Take the top of the topmost band back, and keep taking it.

        See TOPMOST_POLL_MS: every window a session raises lands above this one,
        and this is the only thing that puts it back.  Our own window, so the
        call goes straight through rather than onto the hung-window guard's
        worker thread — it cannot block on anything but ourselves.

        The handle is looked up by this window's own title, and only once:
        ``winfo_id`` on a Tk toplevel is not reliably the top-level HWND.
        """
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
        """Say we are cancelling, and go on saying it.

        A step message still in flight would otherwise flip the line back to
        business as usual while the teardown runs.
        """
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
        # disk and no key ever reached this window, so the words are picked up
        # here.  (Staying on top is _stay_on_top's much faster timer's job.)
        if self._cancel is not None and not self._status_held:
            try:
                if self._cancel.requested():
                    self._hold_status()
            except OSError:
                pass
        try:
            if self._progress_file.exists():
                mtime = self._progress_file.stat().st_mtime
                text = self._progress_file.read_text(encoding="utf-8")
                step, total, message, done = parse_progress(text)

                if done:
                    self._root.destroy()
                    return

                if total > 0:
                    self._content.progress_var.set(step / total * 100)
                if message and not self._status_held:
                    self._content.status_label.configure(text=message)

                self._last_modified = mtime

            # Staleness check: if the file hasn't changed in stale_timeout_s,
            # the orchestrator died holding the cover up.  Close rather than
            # leave the whole desktop behind a panel that will never move.
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

        ``update()`` returns only once Tk has created the window, shown it, and
        served its first paint, so *on_shown* runs when the cover is genuinely
        on screen — a caller that must not act until it is there (shutdown, with
        windows to kill) has a signal it can trust to within a frame.
        """
        self._root.update()
        if on_shown is not None:
            on_shown()
        self._root.mainloop()
