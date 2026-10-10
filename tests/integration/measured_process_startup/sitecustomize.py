import atexit
import contextlib
import sys
import threading

SAVE_EVERY_S = 1.0


class Saver:
    def __init__(self, measurement, every_s: float = SAVE_EVERY_S) -> None:
        self._measurement = measurement
        self._every_s = every_s
        self._stopped = threading.Event()
        self._one_save_at_a_time = threading.Lock()

    def save_until_stopped(self) -> None:
        while not self._stopped.wait(self._every_s):
            with self._one_save_at_a_time, contextlib.suppress(Exception):
                if self._stopped.is_set():
                    return
                self._measurement.save()

    def stop(self) -> None:
        self._stopped.set()
        with self._one_save_at_a_time:
            pass


def started_by_coverage_process_start():
    return getattr(getattr(sys.modules.get("coverage"), "process_startup", None), "coverage", None)


if (measurement := started_by_coverage_process_start()) is not None:
    saver = Saver(measurement)
    # coverage's own final save was registered when its .pth started it, before this module ran,
    # so this stop runs first and that save never overlaps one of these.
    atexit.register(saver.stop)
    threading.Thread(target=saver.save_until_stopped, name="saves coverage", daemon=True).start()
