from __future__ import annotations

import threading

from fun_time.threading_utils import start_daemon_thread


def test_start_daemon_thread_starts_named_daemon_thread():
    ran = threading.Event()

    def worker():
        ran.set()

    thread = start_daemon_thread(target=worker, name="demo-thread")
    thread.join(timeout=1.0)

    assert ran.is_set()
    assert thread.name == "demo-thread"
    assert thread.daemon is True


def test_start_daemon_thread_passes_args_to_target():
    seen: list[tuple[int, int]] = []

    def worker(a: int, b: int):
        seen.append((a, b))

    thread = start_daemon_thread(target=worker, args=(2, 3))
    thread.join(timeout=1.0)

    assert seen == [(2, 3)]
