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
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from fun_time.win32_loader import load_dll
from fun_time.win32_process import is_process_alive
from tests.child_reports import A_STARVED_CHILDS_START_S, pid_written_to
from tests.git_repo import git
from tests.integration import hidden_desktop
from tests.integration.hidden_desktop import (
    _child_environment,
    _close_process_handles,
    _launch_on_desktop,
    _repo_root,
    build_gate_argv,
    build_run_argv,
    close_run_job,
    create_run_job,
    main,
)
from tests.integration.session_lock import SingleInstanceLock, Waiting, hold_integration_lock


def _run_in_a_run_job(probe: str) -> None:
    job = create_run_job()
    pi = _launch_on_desktop(subprocess.list2cmdline([sys.executable, "-c", probe]),
                            None, str(_repo_root()), job)
    try:
        hidden_desktop._wait_for_the_run(pi.hProcess, ceiling_s=A_STARVED_CHILDS_START_S)
    finally:
        _close_process_handles(pi)
        close_run_job(job)


def test_a_hidden_desktop_launch_is_muted_however_the_caller_leaves_the_environment():
    """Off-screen hides a player's window, not its sound, and a repro that
    borrowed this launcher without the mute played the real library aloud
    (2026-09-19).  So the launcher forces the switch on: passed nothing, passed
    an empty environment, or even handed the switch turned off."""
    assert _child_environment(None)["FUN_TIME_MUTE_AUDIO"] == "1"
    assert _child_environment({})["FUN_TIME_MUTE_AUDIO"] == "1"
    assert _child_environment({"FUN_TIME_MUTE_AUDIO": "0"})["FUN_TIME_MUTE_AUDIO"] == "1"


def test_a_hidden_desktop_launch_keeps_the_environment_it_was_handed():
    child = _child_environment({"PATH": "x", "FUN_TIME_RUN_INTEGRATION": "1"})
    assert child["PATH"] == "x"
    assert child["FUN_TIME_RUN_INTEGRATION"] == "1"


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 process creation")
def test_a_launched_child_sees_the_mute_switch_though_the_caller_set_no_environment(tmp_path):
    """End to end: a child started through the launcher with no environment named
    still finds the mute switch set, so nothing off-screen can be heard."""
    report = tmp_path / "mute.txt"
    # Started outside tmp_path and waited out, not polled for its report: the
    # report exists before it is written, and a directory a dying child still
    # stands in cannot be removed -- which failed this test's teardown once.
    _run_in_a_run_job(f"import os, pathlib; pathlib.Path({str(report)!r})"
                      ".write_text(os.environ.get('FUN_TIME_MUTE_AUDIO', 'unset'))")

    assert report.read_text() == "1"


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 process creation")
def test_a_run_stays_below_normal_priority_though_a_process_in_it_raises_itself(tmp_path):
    report = tmp_path / "priority.txt"
    probe = (
        "import ctypes, ctypes.wintypes as wt, pathlib\n"
        "kernel32 = ctypes.WinDLL('kernel32')\n"
        "kernel32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]\n"
        "kernel32.GetPriorityClass.argtypes = [wt.HANDLE]\n"
        "me = wt.HANDLE(-1)\n"
        f"kernel32.SetPriorityClass(me, {subprocess.ABOVE_NORMAL_PRIORITY_CLASS})\n"
        f"pathlib.Path({str(report)!r}).write_text(str(kernel32.GetPriorityClass(me)))\n"
    )
    _run_in_a_run_job(probe)

    assert int(report.read_text()) == subprocess.BELOW_NORMAL_PRIORITY_CLASS


def test_argv_runs_pytest_on_the_integration_dir():
    argv = build_run_argv([])
    assert argv[1:3] == ["-m", "pytest"]
    assert "tests/integration/" in argv


def test_argv_appends_caller_args_after_the_defaults():
    argv = build_run_argv(["-k", "smoke", "-x"])
    assert argv[-3:] == ["-k", "smoke", "-x"]


def test_a_run_naming_a_test_file_runs_that_file_alone():
    argv = build_run_argv(["tests/integration/test_x.py", "-x"])

    assert argv[1:] == ["-m", "pytest", "tests/integration/test_x.py", "-x"]


@pytest.mark.parametrize("spelled", [str(_repo_root() / "tests" / "integration" / "test_x.py"),
                                     "tests\\integration\\test_x.py::test_y"])
def test_a_test_named_by_any_path_to_it_is_run_alone(spelled):
    assert "tests/integration/" not in build_run_argv([spelled])


@pytest.mark.parametrize("leave_out", ["--deselect", "--ignore", "--ignore-glob"])
def test_a_test_named_only_to_be_left_out_leaves_the_rest_of_the_suite_to_run(leave_out):
    argv = build_run_argv([leave_out, "tests/integration/test_x.py"])

    assert argv[1:] == ["-m", "pytest", "tests/integration/", leave_out, "tests/integration/test_x.py"]


def test_a_repeat_run_hands_the_integration_dir_to_the_flake_gate():
    argv = build_gate_argv("origin/main", 45)

    assert argv[1:] == ["-m", "app_support.flake_gate", "--base", "origin/main",
                        "--only", "tests/integration/", "--runs", "10",
                        "--budget-minutes", "45", "--python", sys.executable]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 desktops and jobs")
def test_the_flake_gate_runs_from_an_install_of_its_own_made_before_the_queue(tmp_path):
    """This venv pins the app_support its players run on, and the gate may be in
    a newer one; the tests it repeats still run here, through --python.  Making
    that install can take a download, which no other session should queue for."""
    events = []
    gate_python = tmp_path / "gate" / "Scripts" / "python.exe"

    class _Lock:
        def __enter__(self):
            events.append("lock")

        def __exit__(self, *exc):
            events.append("unlock")

    def install(state_dir):
        events.append("install")
        return gate_python

    def list_changed(python, base, root):
        events.append(f"list with {python}")
        return []

    def launch(*args, environment=None, **kwargs):
        events.append(environment["__PYVENV_LAUNCHER__"])
        return SimpleNamespace(hProcess=None)

    with (patch.object(hidden_desktop, "hold_integration_lock", lambda **_kw: _Lock()),
          patch.object(hidden_desktop, "flake_gate_python", install),
          patch.object(hidden_desktop, "files_with_a_changed_test", list_changed),
          patch.object(hidden_desktop, "_launch_on_desktop", launch),
          patch.object(hidden_desktop, "_close_process_handles", lambda pi: None),
          patch.object(hidden_desktop, "_wait_for_the_run", lambda process, ceiling_s: 0),
          patch.object(hidden_desktop, "HIDDEN_DESKTOP_NAME", f"FunTimeIntegrationUnit{os.getpid()}")):
        hidden_desktop.run_on_hidden_desktop(["--repeat-changed"])
        hidden_desktop.run_on_hidden_desktop([])

    assert events == ["install", f"list with {gate_python}", "lock", str(gate_python), "unlock",
                      "lock", sys.executable, "unlock"]


@contextlib.contextmanager
def _runs_launched_into(launched: list[tuple[str, str]], *, changed_files=(), exit_codes=(),
                        ceilings: list[float] | None = None, places: list[dict] | None = None):
    codes = iter(exit_codes)

    def hold(**place):
        if places is not None:
            places.append(place)
        return contextlib.nullcontext()

    def launch(cmdline, desktop, cwd, job, environment=None):
        launched.append((cmdline, str(environment["__PYVENV_LAUNCHER__"])))
        return SimpleNamespace(hProcess=None)

    def wait(process, ceiling_s):
        if ceilings is not None:
            ceilings.append(ceiling_s)
        return next(codes, 0)

    with (patch.object(hidden_desktop, "hold_integration_lock", hold),
          patch.object(hidden_desktop, "flake_gate_python", lambda state_dir: "gate-python"),
          patch.object(hidden_desktop, "files_with_a_changed_test", lambda *args: list(changed_files)),
          patch.object(hidden_desktop, "_launch_on_desktop", launch),
          patch.object(hidden_desktop, "_close_process_handles", lambda pi: None),
          patch.object(hidden_desktop, "_wait_for_the_run", wait),
          patch.object(hidden_desktop, "HIDDEN_DESKTOP_NAME", f"FunTimeIntegrationUnit{os.getpid()}")):
        yield


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 desktops and jobs")
@pytest.mark.parametrize("args, short", [
    (["-k", "test_x"], True), (["-ktest_x"], True), (["tests/integration/test_x.py"], True),
    (["--repeat-changed"], True), ([], False), (["-x", "--maxfail", "1"], False),
    (["--deselect", "tests/integration/test_x.py::test_y"], False)])
def test_a_run_narrowed_below_the_whole_suite_waits_in_line_as_a_short_run(args, short):
    places: list[dict] = []
    with _runs_launched_into([], places=places):
        hidden_desktop.run_on_hidden_desktop(args)

    assert [place["short"] for place in places] == [short]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 desktops and jobs")
def test_a_repeat_first_runs_each_file_holding_a_changed_test_whole_in_this_venv():
    files = ["tests/integration/test_a.py", "tests/integration/test_b.py"]
    launched: list[tuple[str, str]] = []
    with _runs_launched_into(launched, changed_files=files):
        hidden_desktop.run_on_hidden_desktop(["--repeat-changed"])

    assert launched[0] == (subprocess.list2cmdline(build_run_argv(files)), sys.executable)
    assert "app_support.flake_gate" in launched[1][0]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 desktops and jobs")
def test_a_changed_test_that_fails_beside_the_rest_of_its_file_ends_the_repeat_there():
    launched: list[tuple[str, str]] = []
    with _runs_launched_into(launched, changed_files=["tests/integration/test_a.py"], exit_codes=[1]):
        code = hidden_desktop.run_on_hidden_desktop(["--repeat-changed"])

    assert (code, len(launched)) == (1, 1)


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 desktops and jobs")
def test_a_repeat_names_the_files_it_runs_whole_and_says_when_it_stops_at_them(capsys):
    with _runs_launched_into([], changed_files=["tests/integration/test_a.py"], exit_codes=[1]):
        hidden_desktop.run_on_hidden_desktop(["--repeat-changed"])

    said = capsys.readouterr().err
    assert "whole, once: tests/integration/test_a.py" in said
    assert "so the repeats were not started" in said


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 desktops and jobs")
def test_the_repeats_get_what_running_the_files_whole_left_of_the_budget():
    minutes_into_the_repeat = iter([0.0, 10.0])
    launched: list[tuple[str, str]] = []
    ceilings: list[float] = []
    with (_runs_launched_into(launched, changed_files=["tests/integration/test_a.py"], ceilings=ceilings),
          patch.object(hidden_desktop, "monotonic", lambda: 60 * next(minutes_into_the_repeat))):
        hidden_desktop.run_on_hidden_desktop(["--repeat-changed"])

    gate = launched[1][0].split()
    left = hidden_desktop.REPEAT_BUDGET_MINUTES - 10
    assert gate[gate.index("--budget-minutes") + 1] == str(left)
    assert ceilings == [hidden_desktop.RUN_CEILING_S, left * 60 + hidden_desktop.RUN_CEILING_S]


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 desktops and jobs")
def test_with_no_file_to_run_whole_the_repeats_against_origin_main_get_the_whole_budget():
    launched: list[tuple[str, str]] = []
    ceilings: list[float] = []
    with _runs_launched_into(launched, ceilings=ceilings):
        hidden_desktop.run_on_hidden_desktop(["--repeat-changed"])

    budget = hidden_desktop.REPEAT_BUDGET_MINUTES
    assert launched == [(subprocess.list2cmdline(build_gate_argv("origin/main", budget)), "gate-python")]
    assert ceilings == [budget * 60 + hidden_desktop.RUN_CEILING_S]


def test_the_files_run_whole_are_the_integration_files_holding_a_test_the_branch_changed(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests" / "integration").mkdir(parents=True)
    git(repo, "-c", "init.defaultBranch=main", "init")
    tests = {"tests/integration/test_a.py": "def test_one():\n    assert True\n",
             "tests/integration/test_b.py": "def test_two():\n    assert True\n\n\ndef test_three():\n    assert True\n",
             "tests/test_unit.py": "def test_four():\n    assert True\n"}
    for path, source in tests.items():
        (repo / path).write_text(source, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Before")
    for path in ("tests/integration/test_b.py", "tests/test_unit.py"):
        (repo / path).write_text(tests[path].replace("True", "1"), encoding="utf-8")
    (repo / "tests/integration/test_c.py").write_text("def test_five():\n    assert True\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "After")

    assert hidden_desktop.files_with_a_changed_test(sys.executable, "main~1", repo) == [
        "tests/integration/test_b.py", "tests/integration/test_c.py"]


def test_the_waiting_message_says_whether_the_run_is_short_or_full(capsys):
    hidden_desktop._announce_waiting(True, Waiting(12.0))
    hidden_desktop._announce_waiting(False, Waiting(30.0))
    hidden_desktop._announce_waiting(False, Waiting(40.0, goes_next=True))

    short, full, full_going_next = capsys.readouterr().err.splitlines()
    assert "this short run waits for it, ahead of any full run (12s elapsed)" in short
    assert "lets short runs go first until they have held the line for 20 minutes (30s elapsed)" in full
    assert "this full run waits for it, and short runs now wait for this one (40s elapsed)" in full_going_next


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 named mutexes")
def test_the_waiting_message_reads_what_the_line_reports():
    name = rf"Global\fun_time_test_lock_{uuid.uuid4().hex}"
    blocker = SingleInstanceLock(name)
    assert blocker.acquire(timeout=1.0)
    announced = threading.Event()

    def announce(waiting: Waiting) -> None:
        hidden_desktop._announce_waiting(False, waiting)
        announced.set()

    def wait_in_line() -> None:
        with hold_integration_lock(name=name, notify_every_s=0.05, notify=announce):
            pass

    waiter = threading.Thread(target=wait_in_line, daemon=True)
    waiter.start()
    try:
        assert announced.wait(timeout=10)
    finally:
        blocker.close()
        waiter.join(timeout=10)


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
    with _launches_recorded() as launched:
        assert hidden_desktop._run_the_suite(_python("-c", "import time; time.sleep(60)"),
                                             sys.executable, 1) == hidden_desktop.WEDGED_EXIT_CODE

    assert _wait_until_dead(launched[0])


def test_the_ceiling_leaves_a_green_suite_room_to_finish():
    """Thirteen minutes is a green run here, and the gate's own job times out at
    fifteen.  A ceiling anywhere near either would turn a slow machine into a
    failed run, which is the opposite of what it is for."""
    assert hidden_desktop.RUN_CEILING_S >= 30 * 60


def _python(*python_args: str) -> list[str]:
    return [build_run_argv([])[0], *python_args]


@contextlib.contextmanager
def _launches_recorded():
    launched: list[int] = []

    def launch_and_record(*args, **kwargs):
        pi = _launch_on_desktop(*args, **kwargs)
        launched.append(pi.dwProcessId)
        return pi

    with patch.object(hidden_desktop, "HIDDEN_DESKTOP_NAME", f"FunTimeIntegrationUnit{os.getpid()}"), \
         patch.object(hidden_desktop, "_launch_on_desktop", launch_and_record):
        yield launched


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 process creation")
def test_the_process_a_run_waits_on_is_the_interpreter_itself_running_in_this_venv(tmp_path):
    report = tmp_path / "interpreter.json"
    probe = (f"import json, os, pathlib, sys; pathlib.Path({str(report)!r})"
             ".write_text(json.dumps([os.getpid(), sys.prefix]))")
    with _launches_recorded() as launched:
        assert hidden_desktop._run_the_suite(_python("-c", probe), sys.executable,
                                             hidden_desktop.RUN_CEILING_S) == 0

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
    with _launches_recorded():
        run = threading.Thread(target=lambda: ended.append(hidden_desktop._run_the_suite(
            _python(str(stand_in)), sys.executable, hidden_desktop.RUN_CEILING_S)), daemon=True)
        run.start()
        try:
            with _held_in_its_exit(pid_written_to(pid_file), release):
                run.join(timeout=30)
                assert ended == [3]
        finally:
            run.join(timeout=30)

    assert "Windows has not finished taking its process down" in capsys.readouterr().err


def test_main_hands_its_own_args_to_the_run_and_exits_on_its_verdict():
    with patch.object(hidden_desktop, "run_on_hidden_desktop", return_value=0) as run, \
         patch.object(sys, "argv", ["hidden_desktop", "-k", "main_player"]):
        with pytest.raises(SystemExit) as exit_info:
            main()

    assert exit_info.value.code == 0
    run.assert_called_once_with(["-k", "main_player"])


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
        broker_pid = pid_written_to(pid_file)
    finally:
        _close_process_handles(pi)
        close_run_job(job)

    try:
        assert _wait_until_dead(pi.dwProcessId), "the run itself must still be killed"
        assert is_process_alive(broker_pid), "the broker must break away from the run's job"
    finally:
        subprocess.run(["taskkill", "/PID", str(broker_pid), "/T", "/F"], capture_output=True)
