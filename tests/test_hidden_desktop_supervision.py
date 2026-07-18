"""Unit tests: the runner stands over a run and ends it if Fun Time opens.

The in-pytest watchdog asks pytest to stop, which is the clean way out — fixtures
tear down, the session's children are killed by recorded identity, the temp tree
goes.  It cannot be the only way out: the main thread spends whole seconds inside
blocking subprocess calls (the reap's PowerShell sweeps) where a pending
KeyboardInterrupt simply waits, and a wedged pytest would never notice at all.
So the runner watches too, and after a grace period takes the job down itself.
"""
from __future__ import annotations

from tests.integration.hidden_desktop import supervise_run


class _Wait:
    """A fake process wait: reports 'still running' for *polls*, then 'ended'."""

    def __init__(self, polls: int):
        self.remaining = polls
        self.calls = 0

    def __call__(self, _timeout: float) -> bool:
        self.calls += 1
        self.remaining -= 1
        return self.remaining <= 0


def test_a_run_that_finishes_on_an_idle_machine_is_not_aborted():
    terminated = []

    aborted = supervise_run(
        wait=_Wait(3),
        live_session_found=lambda: False,
        terminate=lambda: terminated.append("kill"),
        grace_seconds=5.0,
        poll_seconds=1.0,
    )

    assert aborted is False
    assert terminated == []


def test_a_run_that_stops_itself_within_the_grace_period_is_not_killed():
    """The watchdog inside pytest got there first — the fixtures tore down
    properly, so there is nothing left for the runner to shoot."""
    terminated = []

    aborted = supervise_run(
        wait=_Wait(4),
        live_session_found=lambda: True,
        terminate=lambda: terminated.append("kill"),
        grace_seconds=30.0,
        poll_seconds=1.0,
    )

    assert aborted is True
    assert terminated == []


def test_the_runner_is_what_says_why_the_run_stopped():
    """pytest captures at the file-descriptor level, so a notice written from
    inside the run goes into its capture buffer and is dropped on the way out —
    the run ends on a bare KeyboardInterrupt with nothing to explain it.  The
    runner is outside that capture, so it is the one that has to say it, and it
    says it when the session appears rather than when the grace runs out.
    """
    announced = []

    supervise_run(
        wait=_Wait(4),
        live_session_found=lambda: True,
        terminate=lambda: None,
        announce=announced.append,
        grace_seconds=30.0,
        poll_seconds=1.0,
    )

    assert len(announced) == 1
    assert "ABORTING" in announced[0]


def test_a_quiet_run_is_never_announced():
    announced = []

    supervise_run(
        wait=_Wait(3),
        live_session_found=lambda: False,
        terminate=lambda: None,
        announce=announced.append,
    )

    assert announced == []


def test_a_run_that_ignores_the_abort_is_killed_once_the_grace_runs_out():
    terminated = []

    aborted = supervise_run(
        wait=lambda _timeout: False,
        live_session_found=lambda: True,
        terminate=lambda: terminated.append("kill"),
        grace_seconds=3.0,
        poll_seconds=1.0,
    )

    assert aborted is True
    assert terminated == ["kill"]
