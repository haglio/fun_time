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

from .hidden_desktop import REFUSED_EXIT_CODE, require_hidden_desktop
from .integration_support import close_udp_sinks
from .session_lock import INTEGRATION_LOCK_NAME, hold_integration_lock

os.environ.pop("QT_QPA_PLATFORM", None)


def pytest_sessionstart(session):
    """Refuse a run that is not on the hidden desktop, before anything launches.

    Earlier than any fixture, so the refusal lands before the run lock is taken,
    before a session is started, and above all before the first reap — which is
    now desktop-scoped with no fallback, and would silently skip its cleanup here
    rather than sweep the machine.
    """
    if sys.platform != "win32":
        return
    try:
        require_hidden_desktop()
    except RuntimeError as wrong_desktop:
        # pytest.exit rather than letting it propagate: an exception out of a
        # sessionstart hook is reported as an INTERNALERROR traceback, which reads
        # as a broken harness instead of what it is — the run being invoked wrongly.
        pytest.exit(str(wrong_desktop), returncode=REFUSED_EXIT_CODE)


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
def _never_inherit_the_integration_flag():
    """Override the unit suite's flag scrub.

    The hidden-desktop runner exported ``FUN_TIME_RUN_INTEGRATION`` on
    purpose, and every production branch under test here reads it live —
    scrubbing it per test would run the session with the real focus steals
    and activations the flag exists to suppress.
    """
    yield


@pytest.fixture(autouse=True)
def _never_mutate_a_real_window():
    """Override the unit suite's window-mutation guard.

    These tests launch the real bridge and position / topmost / activate / minimize
    real native windows on the hidden desktop, so the ``user32`` mutating calls must
    run for real.  A window call resolves handles on the caller's own desktop, so
    nothing here can reach a window of the user's — they are all on ``Default``.
    """
    yield


@pytest.fixture(scope="session", autouse=True)
def _release_the_runs_udp_sinks():
    """Hand back the ports this run bound to catch its own T-Code.

    Session-scoped because the sinks are: every config a run builds binds one,
    and each has to stay bound while any session might still be sending at it.
    """
    yield
    close_udp_sinks()


@pytest.fixture(scope="session", autouse=True)
def _serialize_integration_runs():
    """Hold one machine-wide lock for the entire integration run.

    Multiple worktree agents share this repo and may launch the integration
    suite at the same time.  Runs are isolated from the user's session but not
    from each other: they share one hidden desktop, so a run's leftover-process
    reap (``FunTimeIntegrationSession._reap_leftover_runtime_processes``) finds a
    concurrent run's freshly-spawned players there, and the AHK bridge runs under
    ``#SingleInstance Force``, whose search is desktop-scoped — so a second
    bridge on that desktop evicts the first.  Overlapping runs therefore fail
    flakily on different tests each time.

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
