"""Unit tests for the hidden-desktop integration runner.

The desktop plumbing (create/enumerate) is validated by actually running the suite
through it.  Here we pin the pure decision — what pytest command the hidden desktop
runs — and the job object that guarantees a run cannot outlive itself.
"""
from __future__ import annotations

import subprocess
import sys
import time

import pytest

from fun_time.win32 import is_process_alive
from tests.integration.hidden_desktop import (
    _close_process_handles,
    _launch_on_desktop,
    _repo_root,
    build_pytest_argv,
    close_run_job,
    create_run_job,
)


def test_argv_runs_pytest_on_the_integration_dir():
    argv = build_pytest_argv([])
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pytest"]
    assert "tests/integration/" in argv


def test_argv_appends_caller_args_after_the_defaults():
    argv = build_pytest_argv(["-k", "smoke", "-x"])
    assert argv[-3:] == ["-k", "smoke", "-x"]


def _wait_until_dead(pid: int, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_process_alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 job objects")
def test_a_process_launched_into_the_run_job_dies_when_the_job_closes(tmp_path):
    """The runner holds the job's only handle, so however the runner ends — a
    clean exit, a crash, a kill — Windows terminates whatever the run left
    running.  No integration run can strand a VLC for the next one to trip on."""
    job = create_run_job()
    cmdline = subprocess.list2cmdline([sys.executable, "-c", "import time; time.sleep(60)"])
    pi = _launch_on_desktop(cmdline, None, str(tmp_path), job)
    try:
        assert is_process_alive(pi.dwProcessId), "child should be running, not left suspended"
    finally:
        _close_process_handles(pi)
        close_run_job(job)

    assert _wait_until_dead(pi.dwProcessId)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 job objects")
def test_the_broker_a_run_starts_survives_that_runs_job(tmp_path):
    """The broker is a service, not a child: harem and the user's next Fun Time
    launch keep talking to it after the run that started it is gone.  It is the
    one process a run may leave behind."""
    pid_file = tmp_path / "broker_pid.txt"
    spawn_broker = (
        "import pathlib, subprocess, sys;"
        "from fun_time.orchestrator_broker import broker_launch_kwargs;"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
        " **broker_launch_kwargs());"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid));"
        "import time; time.sleep(60)"
    )
    job = create_run_job()
    cmdline = subprocess.list2cmdline([sys.executable, "-c", spawn_broker])
    pi = _launch_on_desktop(cmdline, None, str(_repo_root()), job)
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not pid_file.exists():
            time.sleep(0.1)
        broker_pid = int(pid_file.read_text())
    finally:
        _close_process_handles(pi)
        close_run_job(job)

    try:
        assert _wait_until_dead(pi.dwProcessId), "the run itself must still be killed"
        assert is_process_alive(broker_pid), "the broker must break away from the run's job"
    finally:
        subprocess.run(["taskkill", "/PID", str(broker_pid), "/T", "/F"], capture_output=True)
