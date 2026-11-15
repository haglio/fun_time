from __future__ import annotations

import os
import sys

from tests import child_reports
from tests.child_reports import (
    StartedProcess,
    a_child_of_this_venv,
    all_gone_within,
    with_what_it_started,
)
from tests.integration import run_clock
from tests.integration.run_clock import keep_time_by
from tests.made_up_clocks import SleepDrivenClock, StarvedRun


def test_a_process_older_than_its_parent_was_started_by_an_earlier_owner_of_the_parents_pid(
        monkeypatch):
    created = {756: 200, 764: 100, 900: 300}
    monkeypatch.setattr(child_reports, "list_child_pids", lambda pid: [764, 900] if pid == 756 else [])
    monkeypatch.setattr(child_reports, "process_started", created.get)

    assert with_what_it_started(756) == [StartedProcess(756, 200), StartedProcess(900, 300)]


def test_a_child_of_this_venv_is_one_interpreter_of_the_venv_running_no_coverage(monkeypatch, tmp_path):
    monkeypatch.setenv("COVERAGE_PROCESS_START", str(tmp_path / "coveragerc"))
    report = tmp_path / "report.txt"
    child = a_child_of_this_venv(
        "import os, sys, pathlib\n"
        f"pathlib.Path({str(report)!r}).write_text("
        "repr((os.getppid(), sys.prefix, 'coverage' in sys.modules)))")
    child.wait()

    assert report.read_text() == repr((os.getpid(), sys.prefix, False))


def test_processes_are_waited_on_to_go_in_the_seconds_the_run_could_run(monkeypatch):
    monkeypatch.setattr(child_reports, "time", SleepDrivenClock())
    looks = iter(range(10_000))
    monkeypatch.setattr(child_reports, "running", lambda _process: next(looks) < 9_999)
    keep_time_by(StarvedRun())
    try:
        assert all_gone_within([StartedProcess(756, 200)], 5.0)
    finally:
        keep_time_by(run_clock.WALL)
