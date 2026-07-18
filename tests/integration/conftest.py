"""Force the real Windows platform for the integration suite, and serialize runs.

Integration tests launch the real bridge and inspect real native windows (the
dashboard, Nau, the satellites), so they must run on the native Qt platform — never the
offscreen platform the unit suite defaults to. The root ``tests/conftest.py`` sets
``QT_QPA_PLATFORM=offscreen`` so routine unit runs don't flash windows; because it is
an ancestor conftest it is imported first, so by the time this module runs the variable
is already ``"offscreen"``.

Remove it here — before any ``QApplication`` is created or any bridge/Nau subprocess is
spawned — so Qt falls back to the native windows platform and every child process the
integration session launches inherits a real platform too. Without this, those windows
would render offscreen and the Win32 inspection helpers would find nothing.
"""
from __future__ import annotations

import os
import sys

import pytest

from .session_lock import INTEGRATION_LOCK_NAME, hold_integration_lock

os.environ.pop("QT_QPA_PLATFORM", None)


def _announce_waiting(seconds: float) -> None:
    """Surface that this run is queued behind another integration run.

    Written to the real stderr so it appears live, rather than being held back
    with the rest of pytest's captured output until the run finishes.
    """
    stream = sys.__stderr__ or sys.stderr
    if stream is not None:
        stream.write(
            f"[integration] another integration run holds {INTEGRATION_LOCK_NAME!r}; "
            f"waiting for it to finish ({seconds:.0f}s elapsed)…\n"
        )
        stream.flush()


@pytest.fixture(autouse=True)
def _never_mutate_a_real_window():
    """Override the unit suite's window-mutation guard.

    These tests launch the real bridge and position / topmost / activate / minimize
    real native windows on the hidden desktop, so the ``user32`` mutating calls must
    run for real.  They cannot reach the user's windows because ``live_session_guard``
    refuses to start a run while Fun Time is open.
    """
    yield


@pytest.fixture(scope="session", autouse=True)
def _serialize_integration_runs():
    """Hold one machine-wide lock for the entire integration run.

    Multiple worktree agents share this repo and may launch the integration
    suite at the same time.  Each run launches the players/AHK and reaps
    leftover app processes (``FunTimeIntegrationSession._reap_leftover_runtime_
    processes``) that force-kills *any* AutoHotkey64/pythonw it finds —
    including a concurrent run's freshly-spawned processes — and the AHK bridge
    runs under ``#SingleInstance Force`` so a second bridge evicts the first.
    Overlapping runs therefore fail flakily on different tests each time.

    Serialize them: only one run's processes are ever live at a time; the rest
    queue here instead of clobbering.  A crashed holder's mutex is auto-released
    by the OS, so a dead run cannot wedge the queue.

    Session-scoped + autouse so the lock is acquired before the first test's
    setup — hence before any module-scoped fixture calls ``start()`` (which runs
    the process sweep) — and released only after the last session has been torn
    down.
    """
    if sys.platform != "win32":
        yield
        return
    with hold_integration_lock(notify=_announce_waiting):
        yield
