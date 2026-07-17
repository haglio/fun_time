"""Loading screen for Fun Time startup.

Runs as a subprocess: ``python -m fun_time.loading_screen <progress_file>``

Displays a tkinter window with a determinate progress bar that polls
a progress file written by the orchestrator.  Auto-closes when the
progress file contains ``DONE`` or when the step count reaches total.
"""
from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import TYPE_CHECKING

from .startup_progress import cancel_file_for

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage


def request_startup_cancel(progress_file: str | Path) -> None:
    """Signal the orchestrator to abort startup by dropping the cancel flag.

    The orchestrator's progress reporter watches for this file and raises at
    its next checkpoint, unwinding startup and tearing down whatever launched.
    """
    cancel_file_for(progress_file).write_text("", encoding="utf-8")


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
STALE_TIMEOUT_S = 60.0

# Distinct from the dashboard's "Fun Time": title-based window lookups must
# never resolve the loading overlay when they mean the dashboard (both are
# python processes whose venv-launcher pids don't own their windows). The
# overlay is borderless, so the title is never rendered anywhere.
WINDOW_TITLE = "Fun Time Loading"


class LoadingScreen:
    def __init__(self, progress_file: Path) -> None:
        self._progress_file = progress_file
        self._last_modified = 0.0
        self._cancelling = False

        BG = "#1a1a2e"
        PINK = "#e94560"
        TROUGH = "#16213e"
        TEXT_DIM = "#c0c0d8"
        HINT_DIM = "#7a7a95"  # subtler than the status line, still legible on BG

        self._root = tk.Tk()
        self._root.title(WINDOW_TITLE)
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
            text="Starting...",
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
            text="Press Esc to cancel",
            font=("Segoe UI", 8),
            fg=HINT_DIM,
            bg=BG,
        )
        self._hint_label.pack()

        # Esc anywhere on the overlay asks the orchestrator to abort startup.
        # The overlay stays up (showing "Cancelling...") until the orchestrator
        # has torn the half-started session down and writes DONE, so nothing
        # half-launched ever flashes into view.
        self._root.bind("<Escape>", self._on_escape)
        self._root.focus_force()

        self._root.after(POLL_MS, self._poll)

    def _on_escape(self, _event: object = None) -> None:
        if self._cancelling:
            return
        self._cancelling = True
        try:
            request_startup_cancel(self._progress_file)
        except OSError:
            pass
        try:
            self._status_label.configure(text="Cancelling...")
            self._hint_label.configure(text="")
        except tk.TclError:
            pass

    def _poll(self) -> None:
        # Startup promotes windows into the TOPMOST band while this overlay
        # is up (the newest topmost window wins), so re-assert on every tick
        # to stay visually on top until destroyed.
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
                # Once the user has asked to cancel, hold the "Cancelling..."
                # status; don't let a late step message flip it back.
                if message and not self._cancelling:
                    self._status_label.configure(text=message)

                if mtime != self._last_modified:
                    self._last_modified = mtime

            # Staleness check: if file hasn't changed in STALE_TIMEOUT_S, close
            if self._last_modified > 0:
                age = time.time() - self._last_modified
                if age > STALE_TIMEOUT_S:
                    self._root.destroy()
                    return

        except (OSError, tk.TclError):
            pass

        try:
            self._root.after(POLL_MS, self._poll)
        except tk.TclError:
            pass  # window already destroyed

    def run(self) -> None:
        self._root.mainloop()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m fun_time.loading_screen <progress_file>", file=sys.stderr)
        sys.exit(1)

    progress_file = Path(sys.argv[1])
    screen = LoadingScreen(progress_file)
    screen.run()


if __name__ == "__main__":
    main()
