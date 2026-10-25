from __future__ import annotations

import os
import subprocess
import sys
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
