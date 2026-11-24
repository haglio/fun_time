from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from app_support.subprocess_utils import tie_to_this_process

from .checkout_overrides import sibling_checkouts_kwargs
from .child_launch import no_child_log, no_console_window
from .process_identity import NAMER
from .win32 import find_window_by_title, wait_for_window
from .window_layout import WindowRect

logger = logging.getLogger(__name__)

TITLE = "Fun Time browser placeholder"
UP_WITHIN_S = 5.0


@dataclass
class BrowserPlaceholder:
    process: subprocess.Popen | None
    hwnd: int

    def take_it_down(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()


def stand_unseen_over(rect: WindowRect, *, sibling_checkouts: str,
                      start: Callable[..., subprocess.Popen] = subprocess.Popen,
                      up_within_s: float = UP_WITHIN_S) -> BrowserPlaceholder:
    process = start(
        [NAMER.named_exe(sys.executable, "BrowserPlaceholder"), "-m", "fun_time.browser_placeholder",
         str(rect.x), str(rect.y), str(rect.width), str(rect.height)],
        **no_child_log(), **no_console_window(), **sibling_checkouts_kwargs(sibling_checkouts))
    tie_to_this_process(process)
    hwnd = wait_for_window(lambda: find_window_by_title(TITLE, exact=True, include_hidden=True),
                           up_within_s)
    if not hwnd:
        logger.warning("The browser placeholder was not up within %.0fs; the browser opens without it",
                       up_within_s)
    return BrowserPlaceholder(process, hwnd)


def main(argv: list[str] | None = None) -> None:
    import tkinter as tk  # noqa: PLC0415

    from shared_ui.palette import LOADING_GROUND, as_hex  # noqa: PLC0415

    x, y, width, height = (int(word) for word in (argv or sys.argv[1:])[:4])
    root = tk.Tk()
    root.withdraw()
    root.title(TITLE)
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=as_hex(LOADING_GROUND))
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.update()
    root.mainloop()


if __name__ == "__main__":
    main()
