"""The cover Fun Time puts over every monitor while its windows are changing.

A session's windows arrive one at a time and leave the same way, so both ends
of one raise a cover and do the work behind it.  The window is borderless and
always on top; it reads how far the work has got from a progress file the
orchestrator writes, and closes itself when that file says the orchestrator is
finished with it.

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

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


def parse_progress(text: str) -> tuple[int, int, str, bool]:
    """Parse a progress file line.

    Returns (step, total, message, done).
    """
    text = text.strip()
    if text == "DONE":
        return 0, 1, "", True
    try:
        parts = text.split("|", 1)
        step_part = parts[0]
        message = parts[1] if len(parts) > 1 else ""
        step_str, total_str = step_part.split("/")
        return int(step_str), int(total_str), message, False
    except (ValueError, IndexError):
        return 0, 1, "", False


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
    except Exception:
        return None


POLL_MS = 200


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

        BG = "#1a1a2e"
        PINK = "#e94560"
        TROUGH = "#16213e"
        TEXT_DIM = "#c0c0d8"
        HINT_DIM = "#7a7a95"  # subtler than the status line, still legible on BG

        self._root = tk.Tk()
        self._root.title(title)
        self._root.resizable(False, False)
        self._root.attributes("-topmost", True)
        self._root.overrideredirect(True)
        self._root.configure(bg=BG)

        # Query virtual-desktop bounding box (spans all monitors) so the
        # overlay covers everything and no windows flash through.
        try:
            import ctypes
            u32 = ctypes.windll.user32
            u32.SetProcessDPIAware()
            vx = u32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
            vy = u32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
            vw = u32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
            vh = u32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
        except Exception:
            vx, vy = 0, 0
            vw = self._root.winfo_screenwidth()
            vh = self._root.winfo_screenheight()

        self._root.geometry(f"{vw}x{vh}+{vx}+{vy}")

        # Use clam theme for full style control
        style = ttk.Style(self._root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "FunTime.Horizontal.TProgressbar",
            troughcolor=TROUGH,
            background=PINK,
            thickness=18,
            borderwidth=0,
        )
        style.configure("FunTime.TFrame", background=BG)

        # Center the content panel on the primary monitor (0,0 origin),
        # not the virtual-desktop midpoint which may fall between monitors.
        frame = ttk.Frame(self._root, padding=24, style="FunTime.TFrame")
        primary_cx = self._root.winfo_screenwidth() // 2 - vx
        primary_cy = self._root.winfo_screenheight() // 2 - vy
        frame.place(x=primary_cx, y=primary_cy, anchor=tk.CENTER)

        # Icon above the title — loaded from icon.ico at the project root.
        self._icon_photo = None  # prevent GC of PhotoImage
        ico_path = Path(__file__).resolve().parent.parent / "icon.ico"
        icon_img = load_icon_image(ico_path, ICON_DISPLAY_SIZE)
        if icon_img is not None:
            try:
                from PIL import ImageTk

                self._icon_photo = ImageTk.PhotoImage(icon_img)
                icon_label = tk.Label(frame, image=self._icon_photo, bg=BG)
                icon_label.pack(pady=(0, 12))
            except Exception:
                pass

        title_label = tk.Label(
            frame,
            text="Fun Time",
            font=("Segoe UI", 18, "bold italic"),
            fg=PINK,
            bg=BG,
        )
        title_label.pack(pady=(0, 10))

        self._status_label = tk.Label(
            frame,
            text=status,
            font=("Segoe UI", 10),
            fg=TEXT_DIM,
            bg=BG,
        )
        self._status_label.pack(pady=(0, 10))

        self._progress_var = tk.DoubleVar(value=0)
        self._progress_bar = ttk.Progressbar(
            frame,
            variable=self._progress_var,
            maximum=100,
            length=360,
            mode="determinate",
            style="FunTime.Horizontal.TProgressbar",
        )
        self._progress_bar.pack(pady=(0, 8))

        self._hint_label = tk.Label(
            frame,
            text=cancel.hint if cancel else "",
            font=("Segoe UI", 8),
            fg=HINT_DIM,
            bg=BG,
        )
        self._hint_label.pack()

        if cancel is not None:
            # Esc anywhere on the overlay asks the orchestrator to stop.  The
            # focus is taken so the key lands here rather than on whatever the
            # session put up last.
            self._root.bind("<Escape>", self._on_escape)
            self._root.focus_force()

        self._root.after(POLL_MS, self._poll)

    def _on_escape(self, _event: object = None) -> None:
        if self._cancel is None or self._status_held:
            return
        self._status_held = True
        try:
            self._cancel.request()
        except OSError:
            pass
        try:
            self._status_label.configure(text=self._cancel.pending)
            self._hint_label.configure(text="")
        except tk.TclError:
            pass

    def _poll(self) -> None:
        # Both ends of a session promote windows into the TOPMOST band while
        # this overlay is up (the newest topmost window wins), so re-assert on
        # every tick to stay visually on top until destroyed.
        self._root.attributes("-topmost", True)
        try:
            if self._progress_file.exists():
                mtime = self._progress_file.stat().st_mtime
                text = self._progress_file.read_text(encoding="utf-8")
                step, total, message, done = parse_progress(text)

                if done or (total > 0 and step >= total):
                    self._root.destroy()
                    return

                if total > 0:
                    self._progress_var.set(step / total * 100)
                if message and not self._status_held:
                    self._status_label.configure(text=message)

                if mtime != self._last_modified:
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
