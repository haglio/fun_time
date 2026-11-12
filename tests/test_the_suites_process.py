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
from PySide6.QtWidgets import QWidget

left_open = []

def test_leaves_a_window_open():
    left_open.append(QWidget())
"""


_COUNTS_WHAT_PYTHON_OWNS_ONCE_THE_SUITES_APPLICATION_IS_GONE = """
import gc, os
from pathlib import Path
import shiboken6
from PySide6.QtCore import QCoreApplication, QObject

def pytest_unconfigure(config):
    count = 0
    for found in gc.get_objects():
        if isinstance(found, QObject) and not isinstance(found, QCoreApplication):
            count += shiboken6.isValid(found) and shiboken6.ownedByPython(found)
    Path(os.environ["LEFT_OWNED_BY_PYTHON"]).write_text(str(count), encoding="utf-8")
"""


_A_QT_OBJECT_LEFT_IN_A_CYCLE_BY_ONE_TEST = """
import threading
from PySide6.QtCore import QObject

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


def _run_pytest_on(test_file: Path, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-p", "tests.conftest", *args, "-q",
         "-o", "addopts=-p no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTEST_ADDOPTS": "", **env},
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )


def test_a_run_ends_on_pytests_verdict_whatever_a_library_does_on_the_way_out(tmp_path: Path):
    test_file = tmp_path / "test_dies_on_the_way_out.py"
    test_file.write_text(_A_LIBRARY_THAT_DIES_ON_THE_WAY_OUT, encoding="utf-8")

    run = _run_pytest_on(test_file)

    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 passed" in run.stdout, run.stdout + run.stderr


def test_python_owns_no_qt_object_once_the_suites_application_is_gone(tmp_path: Path):
    test_file = tmp_path / "test_leaves_a_window_open.py"
    test_file.write_text(_A_WINDOW_LEFT_OPEN_TILL_THE_APPLICATION_GOES, encoding="utf-8")
    (tmp_path / "counts_what_python_owns.py").write_text(
        _COUNTS_WHAT_PYTHON_OWNS_ONCE_THE_SUITES_APPLICATION_IS_GONE, encoding="utf-8")
    left_owned = tmp_path / "left_owned_by_python.txt"

    run = _run_pytest_on(test_file, "-p", "counts_what_python_owns",
                         LEFT_OWNED_BY_PYTHON=str(left_owned),
                         PYTHONPATH=os.pathsep.join(filter(None, [str(tmp_path), os.environ.get("PYTHONPATH")])))

    assert run.returncode == 0, run.stdout + run.stderr
    assert left_owned.read_text(encoding="utf-8") == "0"


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
