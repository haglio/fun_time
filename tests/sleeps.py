from __future__ import annotations

import contextlib
import time
from unittest.mock import MagicMock, patch


class _ClockThatNeverWaits:
    def __init__(self):
        self.sleep = MagicMock()

    def __getattr__(self, name):
        return getattr(time, name)


@contextlib.contextmanager
def sleeps_in(module):
    clock = _ClockThatNeverWaits()
    with patch.object(module, "time", clock):
        yield clock.sleep
