"""Unit tests for the integration test-support harness (teardown safety).

These guard the deterministic child-process cleanup that
``FunTimeIntegrationSession.stop()`` must perform.  ``stop()`` hard-terminates
the orchestrator, so the orchestrator's own graceful ``_shutdown_children()``
never runs and its children (the two satellite VLCs, plus Nau/Genau/dashboard/
audio) are orphaned.  The teardown must therefore kill them itself, by the
exact PIDs recorded in ``bridge_pids.ini`` — not by a racy name+StartTime
heuristic that can silently leave a live ``vlc.exe`` behind.
"""
from __future__ import annotations

import pytest

from tests.integration import integration_support
from tests.integration.integration_support import FunTimeIntegrationSession


@pytest.fixture
def session(cfg_path):
    return FunTimeIntegrationSession(cfg_path)


def _write_bridge_pids(session: FunTimeIntegrationSession, pids: dict[str, int]) -> None:
    pids_file = session.config.paths.state_dir / "bridge_pids.ini"
    pids_file.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(["[pids]", *(f"{k}={v}" for k, v in pids.items())])
    pids_file.write_text(body + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _hermetic_stop(session, monkeypatch):
    """Neutralize the side-effecting halves of stop() so tests stay hermetic.

    The name+StartTime PowerShell sweep and the on-disk vlcrc rewrite are
    exercised elsewhere; here we isolate the deterministic PID-kill path.
    """
    monkeypatch.setattr(session, "_reap_leftover_runtime_processes", lambda: None)


def test_stop_taskkills_every_recorded_child_pid(session, monkeypatch):
    _write_bridge_pids(
        session,
        {
            "nau_pid": 201,
            "portrait_pid": 202,   # satellite VLC
            "landscape_pid": 203,  # satellite VLC
            "dashboard_pid": 0,    # disabled in integration — absent
            "genau_pid": 205,
            "audio_pid": 206,
        },
    )
    killed: list[int] = []
    monkeypatch.setattr(integration_support, "kill_process_tree", killed.append, raising=False)

    session.stop()

    # Every recorded child with a real PID is killed by that exact PID; the
    # zero placeholder (disabled dashboard) is skipped.
    assert sorted(killed) == [201, 202, 203, 205, 206]


def test_stop_survives_missing_bridge_pids(session, monkeypatch):
    """A session that failed before writing bridge_pids.ini must still tear
    down without raising — and without trying to kill anything by PID."""
    killed: list[int] = []
    monkeypatch.setattr(integration_support, "kill_process_tree", killed.append, raising=False)

    session.stop()  # no bridge_pids.ini on disk

    assert killed == []
