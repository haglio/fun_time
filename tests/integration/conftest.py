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

Removing it is also the one thing this file does that a *unit* run can feel, so it is
done only on the hidden desktop; and this file holds the two hooks that refuse a run
anywhere else, one for each way the directory can be reached.
"""
from __future__ import annotations

import os
import sys

import pytest

from .hidden_desktop import REFUSED_EXIT_CODE, on_hidden_desktop, require_hidden_desktop
from .integration_support import close_udp_sinks
from .session_lock import INTEGRATION_LOCK_NAME, hold_integration_lock

# Only on the desktop this suite is allowed to run on.  Importing this file is not
# the same thing as running it: a unit run that merely *recurses* into this
# directory imports it too, and popping the variable there would take the whole
# unit suite off the offscreen platform — every widget test flashing a real window
# onto the user's monitors, before a single integration test had started.
if sys.platform != "win32" or on_hidden_desktop():
    os.environ.pop("QT_QPA_PLATFORM", None)


def _refuse_a_run_off_the_hidden_desktop() -> None:
    """End the run rather than let an integration test reach the user's screen."""
    if sys.platform != "win32":
        return
    try:
        require_hidden_desktop()
    except RuntimeError as wrong_desktop:
        # pytest.exit rather than letting it propagate: an exception out of a
        # hook is reported as an INTERNALERROR traceback, which reads
        # as a broken harness instead of what it is — the run being invoked wrongly.
        pytest.exit(str(wrong_desktop), returncode=REFUSED_EXIT_CODE)


def pytest_sessionstart(session):
    """Refuse a run that is not on the hidden desktop, before anything launches.

    Earlier than any fixture, so the refusal lands before the run lock is taken,
    before a session is started, and above all before the first reap — which is
    now desktop-scoped with no fallback, and would silently skip its cleanup here
    rather than sweep the machine.
    """
    _refuse_a_run_off_the_hidden_desktop()


def pytest_collection_modifyitems(session, config, items):
    """The same refusal, for a run that never named this directory.

    ``pytest_sessionstart`` above can only fire when this conftest is one of the
    run's *initial* conftests — which it is only when the command line names
    ``tests/integration/``.  A run that instead recurses in from ``tests``
    imports this file partway through collection, long after sessionstart has
    passed, so the refusal never ran at all: the suite launched real players, a
    real Nau and a real AHK bridge onto the user's monitors on top of his work.
    That is not hypothetical — it is what ``-c pyproject.toml`` did while the
    ``norecursedirs`` exclusion still lived in a separate pytest.ini.

    Registered by the same import, and called once collection has finished and
    before the first test runs, this hook fires however the directory was
    reached.  ``norecursedirs`` decides what an ordinary run *collects*; this
    decides what is allowed to *launch*, and only the second is a guard.
    """
    _refuse_a_run_off_the_hidden_desktop()


def _announce_waiting(seconds: float) -> None:
    """Surface that this run is queued after another integration run.

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
