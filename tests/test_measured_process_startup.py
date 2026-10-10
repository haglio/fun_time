from __future__ import annotations

import importlib.util
import threading

from tests.integration.hidden_desktop import MEASURED_PROCESS_STARTUP

_SPEC = importlib.util.spec_from_file_location("measured_process_startup",
                                               MEASURED_PROCESS_STARTUP / "sitecustomize.py")
startup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(startup)


class HeldSave:
    def __init__(self) -> None:
        self.started, self.released = threading.Event(), threading.Event()
        self.saves = 0

    def save(self) -> None:
        self.saves += 1
        self.started.set()
        self.released.wait()


class FailsOnce:
    def __init__(self) -> None:
        self.saved = threading.Event()
        self.tries = 0

    def save(self) -> None:
        self.tries += 1
        if self.tries == 1:
            raise RuntimeError("a thread being measured changed what was being saved")
        self.saved.set()


def test_a_save_that_fails_is_tried_again_at_the_next_beat():
    measurement = FailsOnce()
    saver = startup.Saver(measurement, every_s=0.01)
    saving = threading.Thread(target=saver.save_until_stopped)
    saving.start()
    saved = measurement.saved.wait(30)
    saver.stop()
    saving.join()

    assert saved


def test_stopping_waits_out_a_save_under_way_and_no_save_follows_it():
    measurement = HeldSave()
    saver = startup.Saver(measurement, every_s=0.01)
    saving = threading.Thread(target=saver.save_until_stopped)
    saving.start()
    assert measurement.started.wait(30)
    stopping = threading.Thread(target=saver.stop)
    stopping.start()
    stopping.join(timeout=0.2)
    stopped_before_the_save_ended = not stopping.is_alive()
    measurement.released.set()
    stopping.join()
    saving.join()

    assert not stopped_before_the_save_ended
    assert measurement.saves == 1
