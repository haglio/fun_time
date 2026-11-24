from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable, Iterator
from typing import Protocol

from .window_layout import WindowRect

logger = logging.getLogger(__name__)

TITLE = "Fun Time browser placeholder"
UP_WITHIN_S = 2.0
LOOKS_FOR_ITS_END_EVERY_MS = 16


class Placeholder(Protocol):
    def stand(self, rect: WindowRect, *, up: threading.Event, done: threading.Event) -> None: ...


class _BlankTkWindow:
    def stand(self, rect: WindowRect, *, up: threading.Event, done: threading.Event) -> None:
        import tkinter as tk  # noqa: PLC0415

        from shared_ui.palette import LOADING_GROUND, as_hex  # noqa: PLC0415

        root = tk.Tk()
        root.title(TITLE)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=as_hex(LOADING_GROUND))
        root.geometry(f"{rect.width}x{rect.height}+{rect.x}+{rect.y}")
        root.update()
        up.set()

        def until_done() -> None:
            if done.is_set():
                root.destroy()
            else:
                root.after(LOOKS_FOR_ITS_END_EVERY_MS, until_done)

        until_done()
        root.mainloop()


@contextlib.contextmanager
def holding_the_browsers_place(rect: WindowRect, *,
                               placeholder: Placeholder | None = None,
                               start: Callable[[Callable[[], None]], threading.Thread] | None = None,
                               up_within_s: float = UP_WITHIN_S) -> Iterator[None]:
    up, done = threading.Event(), threading.Event()
    standing = (placeholder or _BlankTkWindow()).stand
    thread = (start or _a_thread_of_its_own)(lambda: _stand_or_say_why_not(standing, rect, up, done))
    if not up.wait(up_within_s):
        logger.warning("The browser placeholder was not up within %.0fs; opening the browser anyway",
                       up_within_s)
    try:
        yield
    finally:
        done.set()
        thread.join(up_within_s)


def _stand_or_say_why_not(standing, rect: WindowRect, up: threading.Event,
                          done: threading.Event) -> None:
    try:
        standing(rect, up=up, done=done)
    except Exception:
        logger.exception("The browser placeholder could not stand")
        up.set()


def _a_thread_of_its_own(target: Callable[[], None]) -> threading.Thread:
    thread = threading.Thread(target=target, name="browser-placeholder", daemon=True)
    thread.start()
    return thread
