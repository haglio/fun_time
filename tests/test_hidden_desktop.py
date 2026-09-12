"""Unit tests for the hidden-desktop integration runner.

The desktop plumbing (create/enumerate) is validated by actually running the suite
through it.  Here we pin the pure decisions — what pytest command the hidden desktop
runs and whether it runs at all — plus when a run is over, and the job object that
guarantees a run cannot outlive itself.
"""
from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.win32_loader import load_dll
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


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 process creation")
def test_a_run_that_never_decides_an_exit_code_is_ended_at_the_ceiling_with_its_children():
    with _the_run_runs("-c", "import time; time.sleep(60)") as launched, \
         patch.object(hidden_desktop, "RUN_CEILING_S", 1):
        assert hidden_desktop._run_the_suite([]) == hidden_desktop.WEDGED_EXIT_CODE

    assert _wait_until_dead(launched[0])


def test_the_ceiling_leaves_a_green_suite_room_to_finish():
    """Thirteen minutes is a green run here, and the gate's own job times out at
    fifteen.  A ceiling anywhere near either would turn a slow machine into a
    failed run, which is the opposite of what it is for."""
    assert hidden_desktop.RUN_CEILING_S >= 30 * 60


@contextlib.contextmanager
def _the_run_runs(*python_args: str):
    interpreter = build_pytest_argv([])[0]
    launched: list[int] = []

    def launch_and_record(*args, **kwargs):
        pi = _launch_on_desktop(*args, **kwargs)
        launched.append(pi.dwProcessId)
        return pi

    with patch.object(hidden_desktop, "build_pytest_argv", lambda _extra: [interpreter, *python_args]), \
         patch.object(hidden_desktop, "HIDDEN_DESKTOP_NAME", f"FunTimeIntegrationUnit{os.getpid()}"), \
         patch.object(hidden_desktop, "_launch_on_desktop", launch_and_record):
        yield launched


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 process creation")
def test_the_process_a_run_waits_on_is_the_interpreter_itself_running_in_this_venv(tmp_path):
    report = tmp_path / "interpreter.json"
    probe = (f"import json, os, pathlib, sys; pathlib.Path({str(report)!r})"
             ".write_text(json.dumps([os.getpid(), sys.prefix]))")
    with _the_run_runs("-c", probe) as launched:
        assert hidden_desktop._run_the_suite([]) == 0

    assert json.loads(report.read_text()) == [launched[0], sys.prefix]


_kernel32 = load_dll("kernel32", use_last_error=True)

EXCEPTION_DEBUG_EVENT = 1
CREATE_PROCESS_DEBUG_EVENT = 3
EXIT_PROCESS_DEBUG_EVENT = 5
LOAD_DLL_DEBUG_EVENT = 6
EXCEPTION_BREAKPOINT = 0x80000003
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001


class _DebugEventDetail(ctypes.Union):
    _fields_ = [("hFile", wt.HANDLE), ("ExceptionCode", wt.DWORD), ("_rest", ctypes.c_ubyte * 160)]


class _DEBUG_EVENT(ctypes.Structure):
    _fields_ = [("dwDebugEventCode", wt.DWORD), ("dwProcessId", wt.DWORD),
                ("dwThreadId", wt.DWORD), ("u", _DebugEventDetail)]


_kernel32.DebugActiveProcess.argtypes = [wt.DWORD]
_kernel32.DebugActiveProcess.restype = wt.BOOL
_kernel32.DebugActiveProcessStop.argtypes = [wt.DWORD]
_kernel32.DebugActiveProcessStop.restype = wt.BOOL
_kernel32.WaitForDebugEvent.argtypes = [ctypes.POINTER(_DEBUG_EVENT), wt.DWORD]
_kernel32.WaitForDebugEvent.restype = wt.BOOL
_kernel32.ContinueDebugEvent.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD]
_kernel32.ContinueDebugEvent.restype = wt.BOOL
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.CloseHandle.restype = wt.BOOL


def _answer(event: _DEBUG_EVENT) -> None:
    handled = (event.dwDebugEventCode != EXCEPTION_DEBUG_EVENT
               or event.u.ExceptionCode == EXCEPTION_BREAKPOINT)
    _kernel32.ContinueDebugEvent(event.dwProcessId, event.dwThreadId,
                                 DBG_CONTINUE if handled else DBG_EXCEPTION_NOT_HANDLED)


@contextlib.contextmanager
def _held_in_its_exit(pid: int, release: Path):
    """Debug *pid*, let it go on to exit, and do not let the exit finish.

    A debugged process's last thread waits inside the kernel for its debugger to
    acknowledge the exit, after its exit code is recorded: the state a driver that
    never completes an I/O leaves a process in, reproduced without the driver."""
    if not _kernel32.DebugActiveProcess(pid):
        raise ctypes.WinError(ctypes.get_last_error())
    event = _DEBUG_EVENT()
    unanswered = False
    try:
        release.touch()
        while True:
            if not _kernel32.WaitForDebugEvent(ctypes.byref(event), 30_000):
                raise ctypes.WinError(ctypes.get_last_error())
            unanswered = True
            if event.dwDebugEventCode in (CREATE_PROCESS_DEBUG_EVENT, LOAD_DLL_DEBUG_EVENT) \
                    and event.u.hFile:
                _kernel32.CloseHandle(event.u.hFile)
            if event.dwDebugEventCode == EXIT_PROCESS_DEBUG_EVENT:
                break
            _answer(event)
            unanswered = False
        yield
    finally:
        if unanswered:
            _answer(event)
        _kernel32.DebugActiveProcessStop(pid)


def _pid_written_to(path: Path, timeout: float = 30.0) -> int:
    deadline = time.monotonic() + timeout
    while not (path.exists() and path.read_text()):
        if time.monotonic() > deadline:
            raise TimeoutError(f"nothing wrote a pid to {path} within {timeout:g}s")
        time.sleep(0.05)
    return int(path.read_text())


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 debugging")
def test_a_run_ends_on_the_code_pytest_decided_though_windows_never_finishes_taking_it_down(
        tmp_path, capsys):
    pid_file, release = tmp_path / "pid", tmp_path / "release"
    stand_in = tmp_path / "stand_in.py"
    stand_in.write_text(
        "import os, pathlib, time\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        f"while not pathlib.Path({str(release)!r}).exists():\n"
        "    time.sleep(0.02)\n"
        "os._exit(3)\n",
        encoding="utf-8",
    )
    ended: list[int] = []
    with _the_run_runs(str(stand_in)):
        run = threading.Thread(target=lambda: ended.append(hidden_desktop._run_the_suite([])),
                               daemon=True)
        run.start()
        try:
            with _held_in_its_exit(_pid_written_to(pid_file), release):
                run.join(timeout=30)
                assert ended == [3]
        finally:
            run.join(timeout=30)

    assert "Windows has not finished taking its process down" in capsys.readouterr().err


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
