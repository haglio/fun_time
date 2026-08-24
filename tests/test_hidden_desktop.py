"""Unit tests for the hidden-desktop integration runner.

The desktop plumbing (create/enumerate) is validated by actually running the suite
through it.  Here we pin the pure decisions — what pytest command the hidden desktop
runs and whether it runs at all — plus the job object that guarantees a run cannot
outlive itself.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fun_time.win32 import is_process_alive
from tests.integration import hidden_desktop, integration_support
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
    argv = build_pytest_argv([], Path("report.xml"))
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "pytest"]
    assert "tests/integration/" in argv


def test_argv_appends_caller_args_after_the_defaults():
    argv = build_pytest_argv(["-k", "smoke", "-x"], Path("report.xml"))
    assert argv[-3:] == ["-k", "smoke", "-x"]


def test_argv_asks_pytest_for_the_machine_readable_report_the_run_record_needs():
    """The child's output streams straight to whoever ran the runner, so the
    counts that go in ``docs/integration-runs.md`` cannot be scraped back out of
    it — pytest writes them to a report instead."""
    report_path = Path("somewhere") / "report.xml"

    assert f"--junit-xml={report_path}" in build_pytest_argv([], report_path)


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


def test_a_finished_run_is_recorded_against_the_code_it_ran(monkeypatch):
    """The runner is the only thing that watches a whole run end, so it is the
    only thing that can file one — there is no CI here and no hook.  It has to
    read back the very report path it handed pytest, or the row it writes
    describes a run it never saw."""
    monkeypatch.delenv("FUN_TIME_RUN_INTEGRATION", raising=False)
    with patch.object(hidden_desktop, "_run_pytest_bound_to_the_desktop", return_value=1) as run, \
         patch.object(hidden_desktop, "record_run") as record:
        code = hidden_desktop.run_on_hidden_desktop(["-k", "nau"])

    assert code == 1
    recorded = record.call_args.kwargs
    assert recorded["exit_code"] == 1
    assert recorded["extra_args"] == ["-k", "nau"]
    assert recorded["repo_root"] == _repo_root()
    assert f"--junit-xml={recorded['report_path']}" in run.call_args.args[0]


def test_the_run_record_names_the_player_core_the_run_actually_launched():
    """A worktree pinning an unlanded sibling runs that checkout, so the row has
    to be built from the same directories the run's children get — read through
    the production helper rather than re-derived here."""
    with patch.object(hidden_desktop, "_run_pytest_bound_to_the_desktop", return_value=0), \
         patch.object(hidden_desktop, "record_run") as record, \
         patch.object(integration_support, "checkout_project_dirs",
                      return_value=os.pathsep.join(["/checkouts/genau", "/checkouts/player_core"])):
        hidden_desktop.run_on_hidden_desktop([])

    assert record.call_args.kwargs["project_dirs"] == ["/checkouts/genau", "/checkouts/player_core"]


def test_a_recorder_that_breaks_cannot_turn_a_green_suite_red(capsys):
    """The exit code is the product; the row is bookkeeping.  If writing the row
    ever throws — git gone, the file read-only — the runner still has to hand
    back what pytest said, and say out loud that the record is the thing that
    broke."""
    with patch.object(hidden_desktop, "_run_pytest_bound_to_the_desktop", return_value=0), \
         patch.object(hidden_desktop, "record_run", side_effect=OSError("no room on device")):
        code = hidden_desktop.run_on_hidden_desktop([])

    assert code == 0
    assert "no room on device" in capsys.readouterr().err
