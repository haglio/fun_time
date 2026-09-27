"""Unit tests: every sweep of the machine's processes is bounded and windowless.

A sweep asks WMI, through PowerShell, which processes match a pattern -- and WMI
under load does not always answer.  An unbounded one took a whole integration
run down on 2026-09-26: 240 seconds inside ``subprocess.run``'s own pipe reader,
with the query's PowerShell alive and silent.  The same call sits on the path
every session start takes, where that silence holds the room's opening instead.
"""
from __future__ import annotations

import logging
import subprocess
from unittest.mock import patch

from fun_time import process_sweep
from fun_time.process_sweep import SWEEP_BUDGET_S, sweep_processes


def test_a_sweep_that_reads_runs_powershell_windowless_and_bounded():
    with patch.object(process_sweep.subprocess, "run") as run, \
         patch.object(process_sweep, "subprocess_window_kwargs",
                      return_value={"creationflags": 8}):
        run.return_value.stdout = "1234\n"

        read = sweep_processes("Get-CimInstance Win32_Process", read_output=True)

    assert read == "1234\n"
    argv = run.call_args.args[0]
    assert argv[:4] == ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden"]
    assert argv[-1] == "Get-CimInstance Win32_Process"
    assert run.call_args.kwargs["timeout"] == SWEEP_BUDGET_S
    assert run.call_args.kwargs["check"] is False
    assert run.call_args.kwargs["creationflags"] == 8


def test_a_sweep_that_only_kills_asks_for_no_output_at_all():
    """Reading is what stops a caller: ``capture_output`` gives
    ``subprocess.run`` two reader threads to join, and a silent child holds
    both until its budget runs out."""
    with patch.object(process_sweep.subprocess, "run") as run, \
         patch.object(process_sweep, "subprocess_window_kwargs", return_value={}):

        assert sweep_processes("Stop-Process -Id 1234") == ""

    assert run.call_args.kwargs.get("capture_output") is not True


def test_a_caller_with_less_time_than_that_says_how_long_it_will_wait():
    """A sweep inside a wait of its own cannot be given longer than that wait."""
    with patch.object(process_sweep.subprocess, "run") as run, \
         patch.object(process_sweep, "subprocess_window_kwargs", return_value={}):

        sweep_processes("Get-CimInstance Win32_Process", read_output=True, budget_s=2.5)

    assert run.call_args.kwargs["timeout"] == 2.5


def test_a_sweep_that_outruns_its_budget_is_given_up_on_rather_than_raised(caplog):
    with patch.object(process_sweep.subprocess, "run",
                      side_effect=subprocess.TimeoutExpired("powershell.exe", SWEEP_BUDGET_S)), \
         patch.object(process_sweep, "subprocess_window_kwargs", return_value={}), \
         caplog.at_level(logging.WARNING):

        assert sweep_processes("Get-CimInstance Win32_Process", read_output=True) == ""

    assert "did not answer" in caplog.text
    assert str(int(SWEEP_BUDGET_S)) in caplog.text
