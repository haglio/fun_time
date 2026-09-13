from __future__ import annotations

import threading
import time

from fun_time import windows_bridge_orchestrator
from tests.sleeps import sleeps_in


def test_a_sleep_on_another_thread_is_not_the_modules():
    sleeper_ready, go = threading.Event(), threading.Event()

    def sleep_elsewhere():
        sleeper_ready.set()
        go.wait()
        time.sleep(0)

    sleeper = threading.Thread(target=sleep_elsewhere)
    sleeper.start()
    sleeper_ready.wait()
    with sleeps_in(windows_bridge_orchestrator) as slept:
        go.set()
        sleeper.join()

    slept.assert_not_called()


def test_the_module_still_tells_the_time():
    with sleeps_in(windows_bridge_orchestrator):
        told = windows_bridge_orchestrator.time.monotonic()

    assert abs(told - time.monotonic()) < 60
