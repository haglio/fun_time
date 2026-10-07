from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Iterable, Iterator

TIME_TO_END_S = 10.0


class ThreadsLeftRunning(AssertionError):
    def __init__(self, threads: Iterable[threading.Thread]) -> None:
        super().__init__("still running: " + ", ".join(sorted(thread.name for thread in threads)))


def _still_running(threads: list[threading.Thread], *, within_s: float) -> list[threading.Thread]:
    deadline = time.monotonic() + within_s
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return [thread for thread in threads if thread.is_alive()]


@contextlib.contextmanager
def no_thread_outlives_this(*, within_s: float = TIME_TO_END_S) -> Iterator[None]:
    before = set(threading.enumerate())
    yield
    started = [thread for thread in threading.enumerate() if thread not in before]
    if left := _still_running(started, within_s=within_s):
        raise ThreadsLeftRunning(left)
