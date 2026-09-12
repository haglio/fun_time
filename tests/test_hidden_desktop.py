"""Unit tests for the hidden-desktop integration runner.

The desktop plumbing (create/enumerate) is validated by actually running the suite
through it.  Here we pin the pure decisions — what pytest command the hidden desktop
runs and whether it runs at all — plus the job object that guarantees a run cannot
outlive itself.
"""
from __future__ import annotations

import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fun_time.win32_process import is_process_alive
from tests.integration import hidden_desktop
from tests.integration.hidden_desktop import (
    _close_process_handles,
    _launch_on_desktop,
    _repo_root,
    build_pytest_argv,
    close_run_job,
    create_run_job,
    main,
)


def test_argv_runs_pytest_on_the_integration_dir():
    argv = build_pytest_argv([])
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pytest"]
    assert "tests/integration/" in argv


def test_argv_appends_caller_args_after_the_defaults():
    argv = build_pytest_argv(["-k", "smoke", "-x"])
    assert argv[-3:] == ["-k", "smoke", "-x"]


def test_the_queue_is_waited_out_before_pytest_is_started():
    """A run that has to queue must do its waiting OUT HERE, not inside pytest.

    Held session-scoped in the conftest, the wait was charged to the first
    test's setup and pytest's 240 s per-test timeout killed the run: three runs
    in a row reported a timeout in test_branch_session_integration when all
    that had happened was another agent's suite holding the machine-wide lock.
    A queued run is not a failing run.
    """
    events: list[str] = []

    class _Lock:
        def __enter__(self):
            events.append("lock")
            return self

        def __exit__(self, *exc):
            events.append("unlock")

    def fake_launch(*_args, **_kwargs):
        events.append("pytest")
        raise _StopTheRun

    with patch.object(hidden_desktop, "hold_integration_lock", lambda **_kw: _Lock()), \
         patch.object(hidden_desktop, "_launch_on_desktop", fake_launch), \
         pytest.raises(_StopTheRun):
        hidden_desktop.run_on_hidden_desktop([])

    assert events == ["lock", "pytest", "unlock"]


class _StopTheRun(Exception):
    """Ends the run at the point the child would have been launched."""


def test_a_run_that_never_finishes_is_ended_rather_than_waited_on_forever():
    """pytest's per-test timeout runs on a thread, and a thread cannot interrupt
    a call blocked inside Windows — so a wedged test prints its stack and the run
    then sits there.  The wait used to have no ceiling at all, and the
    machine-wide lock is held around it, so one wedge left every other session's
    suite waiting on a run that was never going to finish.  The job object
    closing on the way out is what takes the children with it.
    """
    closed: list[object] = []

    with patch.object(hidden_desktop._kernel32, "WaitForSingleObject",
                      return_value=hidden_desktop.WAIT_TIMEOUT) as wait, \
         patch.object(hidden_desktop, "_launch_on_desktop",
                      return_value=SimpleNamespace(hProcess=object())), \
         patch.object(hidden_desktop, "_close_process_handles", lambda pi: None), \
         patch.object(hidden_desktop, "close_run_job", closed.append), \
         patch.object(hidden_desktop, "create_run_job", object):
        code = hidden_desktop._run_the_suite([])

    assert code == hidden_desktop.WEDGED_EXIT_CODE
    assert wait.call_args.args[1] == hidden_desktop._ceiling_ms()
    assert len(closed) == 1


def test_the_ceiling_leaves_a_green_suite_room_to_finish():
    """Thirteen minutes is a green run here, and the gate's own job times out at
    fifteen.  A ceiling anywhere near either would turn a slow machine into a
    failed run, which is the opposite of what it is for."""
    assert hidden_desktop.RUN_CEILING_S >= 30 * 60


def test_main_hands_its_own_args_to_the_run_and_returns_its_code():
    with patch.object(hidden_desktop, "run_on_hidden_desktop", return_value=0) as run, \
         patch.object(sys, "argv", ["hidden_desktop", "-k", "nau"]):
        with pytest.raises(SystemExit) as exit_info:
            main()

    assert exit_info.value.code == 0
    run.assert_called_once_with(["-k", "nau"])


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
    running.  No integration run can strand a player for the next one to trip on."""
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
    one process a run may leave."""
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
