"""Unit tests for the integration test-support harness (teardown safety).

These guard the deterministic child-process cleanup that
``FunTimeIntegrationSession.stop()`` must perform.  ``stop()`` hard-terminates
the orchestrator, so the orchestrator's own graceful ``_shutdown_children()``
never runs and its children (the two satellites, plus Nau/Genau/dashboard/
audio) are orphaned.  The teardown must therefore kill them itself, by the
exact processes recorded in ``bridge_pids.ini`` — PID *and* creation time, so a
PID Windows has since recycled is recognised rather than shot.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from fun_time import windows_bridge_orchestrator
from fun_time.windows_bridge_orchestrator import ChildProcess
from tests.integration.integration_support import FunTimeIntegrationSession


@pytest.fixture
def session(cfg_path):
    return FunTimeIntegrationSession(cfg_path)


def _write_bridge_pids(session: FunTimeIntegrationSession, children: dict[str, ChildProcess]) -> None:
    pids_file = session.config.paths.state_dir / "bridge_pids.ini"
    pids_file.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(
        [
            "[pids]",
            *(f"{key}={child.pid}" for key, child in children.items()),
            "[created_at]",
            *(f"{key}={child.created_at}" for key, child in children.items()),
        ]
    )
    pids_file.write_text(body + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _hermetic_stop(session, monkeypatch):
    """Neutralize the side-effecting halves of stop() so tests stay hermetic.

    The desktop-scoped leftover sweep is exercised elsewhere; here we isolate
    the deterministic identity-checked kill path.
    """
    monkeypatch.setattr(session, "_reap_leftover_runtime_processes", lambda: None)


def test_stop_taskkills_every_recorded_child(session):
    _write_bridge_pids(
        session,
        {
            "nau_pid": ChildProcess(201, 2010),
            "portrait_pid": ChildProcess(202, 2020),   # satellite
            "landscape_pid": ChildProcess(203, 2030),  # satellite
            "dashboard_pid": ChildProcess(0, 0),       # disabled in integration — absent
            "genau_pid": ChildProcess(205, 2050),
            "audio_pid": ChildProcess(206, 2060),
        },
    )
    killed: list[int] = []
    with patch.object(windows_bridge_orchestrator, "get_process_creation_time", side_effect=lambda pid: pid * 10), \
         patch.object(windows_bridge_orchestrator, "kill_process_tree", killed.append):
        session.stop()

    # Every recorded child with a real PID is killed by that exact PID; the
    # zero placeholder (disabled dashboard) is skipped.
    assert sorted(killed) == [201, 202, 203, 205, 206]


def test_stop_does_not_kill_a_recorded_pid_windows_recycled(session):
    """Killing a recycled PID is how a run murders another run's pytest: the
    dead child's PID now belongs to somebody else."""
    _write_bridge_pids(session, {"genau_pid": ChildProcess(205, 2050)})
    killed: list[int] = []
    with patch.object(windows_bridge_orchestrator, "get_process_creation_time", return_value=9999), \
         patch.object(windows_bridge_orchestrator, "kill_process_tree", killed.append):
        session.stop()

    assert killed == []


def test_stop_survives_missing_bridge_pids(session):
    """A session that failed before writing bridge_pids.ini must still tear
    down without raising — and without trying to kill anything by PID."""
    killed: list[int] = []
    with patch.object(windows_bridge_orchestrator, "kill_process_tree", killed.append):
        session.stop()  # no bridge_pids.ini on disk

    assert killed == []
