from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.child_reports import all_gone_within, pid_written_to, running, with_what_it_started

ROOT = Path(__file__).resolve().parent.parent

_RAISER = """
import gc, pathlib, subprocess, sys, time
from fun_time.win32_job import tie_to_this_process
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                         creationflags=subprocess.CREATE_NO_WINDOW)
tie_to_this_process(child)
gc.collect()
pathlib.Path({report!r}).write_text(str(child.pid))
time.sleep(60)
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 job objects")
def test_a_child_tied_to_the_process_that_started_it_ends_the_moment_that_process_does(
        tmp_path: Path):
    """The loading and closing screens are tied this way to the Fun Time that
    raised them, so a Fun Time that crashes takes its screen with it.  The
    child here is a venv's python, which runs the real interpreter as a
    child of its own: both have to go."""
    report = tmp_path / "child_pid.txt"
    raiser = subprocess.Popen(
        [sys.executable, "-c", _RAISER.format(report=str(report))],
        cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        child_and_its_own = with_what_it_started(pid_written_to(report, by=raiser))
        assert all(map(running, child_and_its_own)), (
            "the tie ended the child while the process that started it was still running")

        raiser.kill()
        raiser.wait()

        assert all_gone_within(child_and_its_own, 10.0), (
            "the child outlived the process it was tied to")
    finally:
        if raiser.poll() is None:
            raiser.kill()
            raiser.wait()
