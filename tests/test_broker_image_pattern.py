"""The broker sweep has to reach a broker running under its own name.

``stop_broker_processes`` kills the broker and its tray by matching image name
and command line.  The broker names its own processes now -- it starts through
``Broker-Broker.exe`` and ``Broker-Tray.exe`` rather than a bare interpreter --
so a sweep still matching only ``pythonw.exe`` walks past the thing it came to
stop, and a session that meant to take the broker down leaves it running.

The pattern is derived from the same rule the broker names by rather than
spelled out here, so the two cannot drift.  Derived and not imported: this repo
launches the broker and never imports it.
"""
from __future__ import annotations

import re
from unittest.mock import patch

from app_support.process_identity import ProcessNamer

from fun_time.orchestrator_broker import BROKER_IMAGE_PATTERN
from fun_time.windows_bridge_startup import broker_process_started_at, stop_broker_processes

BROKER = ProcessNamer("Broker")


def test_it_matches_the_names_the_broker_launches_under():
    for role in ("Broker", "Tray"):
        name = BROKER.exe_name("pythonw.exe", role)
        assert re.match(BROKER_IMAGE_PATTERN, name), name


def test_it_still_matches_a_broker_that_could_not_be_named():
    # The copy is best-effort, so a broker can arrive under the plain
    # interpreter and must stay reachable.
    for name in ("pythonw.exe", "python.exe", "py.exe"):
        assert re.match(BROKER_IMAGE_PATTERN, name), name


def test_it_leaves_this_session_s_own_processes_alone():
    """The sweep force-kills what it matches.  Fun Time's own children are named
    too, and nothing here should be able to reach them -- the command-line half
    bounds it as well, but the image half must not be the thing that saves us."""
    for name in ("FunTime-Nau.exe", "FunTime-Dashboard.exe", "notepad.exe"):
        assert not re.match(BROKER_IMAGE_PATTERN, name), name


def test_the_sweep_actually_uses_it():
    with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        stop_broker_processes()

    ps_command = run.call_args[0][0][-1]
    assert BROKER_IMAGE_PATTERN in ps_command
    # Still bounded by what the process is running, not by its name alone.
    assert "osr2_broker" in ps_command


def test_the_startup_probe_actually_uses_it():
    """``ensure_broker`` asks when the running broker started, to restart one
    older than its own code.  Matching the bare interpreters alone, the probe
    answered None for a named broker, so the restart could never fire and a
    stale broker went on dropping every verb newer than itself (bug 10)."""
    with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
        "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
    ):
        broker_process_started_at()

    ps_command = run.call_args[0][0][-1]
    assert BROKER_IMAGE_PATTERN in ps_command
    assert "osr2_broker" in ps_command
