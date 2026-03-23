from __future__ import annotations

import threading
from collections.abc import Callable


def start_daemon_thread(
    *,
    target: Callable,
    args: tuple = (),
    name: str | None = None,
) -> threading.Thread:
    thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    thread.start()
    return thread
