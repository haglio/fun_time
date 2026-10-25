from __future__ import annotations

import gc
import os
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_A_LIBRARY_THAT_DIES_ON_THE_WAY_OUT = """
import atexit, os

def test_passes():
    atexit.register(os._exit, 7)
"""


_A_WINDOW_LEFT_OPEN_TILL_THE_APPLICATION_GOES = """
import gc, os, weakref
from pathlib import Path
from PyQt6 import sip
from PyQt6.QtCore import QCoreApplication, QObject
from PyQt6.QtWidgets import QApplication, QWidget

left_open, watching = [], []

def owned_by_python():
    count = 0
    for found in gc.get_objects():
        try:
            if isinstance(found, QObject) and not isinstance(found, QCoreApplication):
                count += not sip.isdeleted(found) and sip.ispyowned(found)
        except ReferenceError:
            pass
    return count

def say_what_is_left(_gone):
    Path(os.environ["LEFT_FOR_PYQT"]).write_text(str(owned_by_python()), encoding="utf-8")

def test_leaves_a_window_open():
    left_open.append(QWidget())
    watching.append(weakref.ref(QApplication.instance(), say_what_is_left))
"""


_A_QT_OBJECT_LEFT_IN_A_CYCLE_BY_ONE_TEST = """
import threading
from PyQt6.QtCore import QObject

gone_on = []

class InACycle(QObject):
    def __init__(self):
        super().__init__()
        self.itself = self

    def __del__(self):
        gone_on.append(threading.current_thread().name)

def test_leaves_a_qt_object_in_a_cycle():
    InACycle()

def test_finds_it_let_go_on_the_main_thread():
    assert gone_on == ["MainThread"]
"""


def _run_pytest_on(test_file: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-p", "tests.conftest", "-q",
         "-o", "addopts=-p no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTEST_ADDOPTS": "", **env},
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def test_a_run_ends_on_pytests_verdict_whatever_a_library_does_on_the_way_out(tmp_path: Path):
    test_file = tmp_path / "test_dies_on_the_way_out.py"
    test_file.write_text(_A_LIBRARY_THAT_DIES_ON_THE_WAY_OUT, encoding="utf-8")

    run = _run_pytest_on(test_file)

    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 passed" in run.stdout, run.stdout + run.stderr


def test_pyqt_finds_nothing_to_delete_when_the_suites_application_goes(tmp_path: Path):
    test_file = tmp_path / "test_leaves_a_window_open.py"
    test_file.write_text(_A_WINDOW_LEFT_OPEN_TILL_THE_APPLICATION_GOES, encoding="utf-8")
    left_for_pyqt = tmp_path / "left_for_pyqt.txt"

    run = _run_pytest_on(test_file, LEFT_FOR_PYQT=str(left_for_pyqt))

    assert run.returncode == 0, run.stdout + run.stderr
    assert left_for_pyqt.read_text(encoding="utf-8") == "0"


def test_a_thread_busy_making_objects_starts_no_collection():
    started_on: list[str] = []

    def note(phase: str, _info: dict) -> None:
        if phase == "start":
            started_on.append(threading.current_thread().name)

    gc.callbacks.append(note)
    try:
        allocator = threading.Thread(target=lambda: [[] for _ in range(100_000)], name="allocator")
        allocator.start()
        allocator.join()
    finally:
        gc.callbacks.remove(note)

    assert "allocator" not in started_on


def test_what_a_test_leaves_in_a_cycle_goes_on_the_main_thread_before_the_next_test(tmp_path: Path):
    test_file = tmp_path / "test_left_in_a_cycle.py"
    test_file.write_text(_A_QT_OBJECT_LEFT_IN_A_CYCLE_BY_ONE_TEST, encoding="utf-8")

    run = _run_pytest_on(test_file)

    assert run.returncode == 0, run.stdout + run.stderr
    assert "2 passed" in run.stdout, run.stdout + run.stderr
