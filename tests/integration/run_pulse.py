from __future__ import annotations

import contextlib
import threading
from pathlib import Path

from app_support.file_channel import read_whole, write_whole

from .run_clock import Budget

PULSE_ENV = "FUN_TIME_INTEGRATION_RUN_PULSE"
PULSE_EVERY_S = 5.0
STOPPED_AFTER_S = 300.0


class Pulse:
    def __init__(self, path: Path, every_s: float = PULSE_EVERY_S) -> None:
        self._path = path
        self._test = ""
        self._beats = 0
        self._writing = threading.Lock()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._keep_beating, args=(every_s,), daemon=True,
                                        name="integration-run-pulse")
        self._thread.start()

    def on_test(self, test: str) -> None:
        self._test = test
        self._beat()

    def stop(self) -> None:
        self._stopped.set()
        self._thread.join()
        with contextlib.suppress(OSError):
            self._path.unlink(missing_ok=True)

    def _keep_beating(self, every_s: float) -> None:
        self._beat()
        while not self._stopped.wait(every_s):
            self._beat()

    def _beat(self) -> None:
        with self._writing, contextlib.suppress(OSError):
            self._beats += 1
            write_whole(self._path, f"{self._beats}\n{self._test}")


class PulseWatch:
    def __init__(self, path: Path, stopped_after_s: float = STOPPED_AFTER_S) -> None:
        self._path = path
        self._stopped_after_s = stopped_after_s
        self._last: str | None = None
        self._budget: Budget | None = None

    def has_stopped(self) -> bool:
        try:
            beat = read_whole(self._path)
        except OSError:
            beat = None
        if beat != self._last:
            self._last = beat
            self._budget = None if beat is None else Budget(self._stopped_after_s)
        return self._budget is not None and self._budget.expired()

    def last_test(self) -> str:
        return (self._last or "").partition("\n")[2]
