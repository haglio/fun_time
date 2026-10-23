from __future__ import annotations

import contextlib
import subprocess
import sys
from pathlib import Path

import pytest

from tests.child_reports import (
    THE_INTERPRETER_NOT_A_VENVS_LAUNCHER,
    StartedProcess,
    all_gone_within,
    an_interpreter_asleep,
    an_interpreter_running,
    running,
)
from tests.process_state import ProcessesLeftRunning, no_process_outlives_this, the_guard_for

ROOT = Path(__file__).resolve().parent.parent

_STARTS_ONE_AND_ENDS = (
    "import subprocess, sys;"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(2)', 'outlived-its-parent'])"
)

_RECORDS_ITS_EXIT_CODE_HALF_A_SECOND_BEFORE_IT_ENDS = (
    "import ctypes, time;"
    "ctypes.windll.ntdll.NtTerminateProcess(None, 7);"
    "time.sleep(0.5)"
)

_LEAVES_ONE_RUNNING_UNTIL_TOLD_TO_END = (
    "import subprocess, sys;"
    "from tests.child_reports import THE_INTERPRETER_NOT_A_VENVS_LAUNCHER;"
    "from tests.process_state import the_job_this_process_holds;"
    "the_job_this_process_holds();"
    "print(subprocess.Popen([THE_INTERPRETER_NOT_A_VENVS_LAUNCHER, '-c', 'import time; time.sleep(60)']).pid,"
    " flush=True);"
    "sys.stdin.readline()"
)


def test_a_process_started_inside_and_never_ended_is_named():
    with contextlib.ExitStack() as afterwards:
        with pytest.raises(ProcessesLeftRunning, match="never-told-to-end"):
            with no_process_outlives_this(within_s=0.05):
                afterwards.enter_context(an_interpreter_asleep("never-told-to-end"))


def test_a_process_whose_parent_has_ended_is_still_named():
    with pytest.raises(ProcessesLeftRunning, match="outlived-its-parent") as raised:
        with no_process_outlives_this(within_s=0.05):
            subprocess.run([THE_INTERPRETER_NOT_A_VENVS_LAUNCHER, "-c", _STARTS_ONE_AND_ENDS], check=True)

    assert all_gone_within(raised.value.processes, 10.0)


def test_a_process_that_ends_a_moment_later_is_waited_for_until_it_has():
    with contextlib.ExitStack() as afterwards:
        with no_process_outlives_this():
            ending = afterwards.enter_context(
                an_interpreter_running(_RECORDS_ITS_EXIT_CODE_HALF_A_SECOND_BEFORE_IT_ENDS))

        assert ending.poll() is not None


def test_a_process_already_running_before_it_began_is_left_alone():
    with no_process_outlives_this(), an_interpreter_asleep():
        with no_process_outlives_this(within_s=0.05):
            pass


def test_what_a_process_leaves_running_ends_with_it():
    with subprocess.Popen([sys.executable, "-c", _LEAVES_ONE_RUNNING_UNTIL_TOLD_TO_END], cwd=ROOT,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as holder:
        left = StartedProcess.of(int(holder.stdout.readline()))
        assert running(left)

        holder.stdin.close()

    assert all_gone_within([left], 10.0)


def test_every_test_runs_inside_it(request):
    assert "_no_process_outlives_its_test" in request.fixturenames


def test_an_integration_test_is_left_to_the_job_its_runner_holds():
    with contextlib.ExitStack() as afterwards:
        with the_guard_for(Path(__file__).parent / "integration" / "test_a_session.py"):
            afterwards.enter_context(an_interpreter_asleep())
