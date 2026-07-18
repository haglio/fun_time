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

import _thread
import os
import sys
import threading

import pytest

from fun_time.live_session import CLAIM_PATH_ENV_VAR

from .live_session_guard import watch_for_live_session
from .session_lock import INTEGRATION_LOCK_NAME, hold_integration_lock

os.environ.pop("QT_QPA_PLATFORM", None)

# Likewise restore the real live-session claim path. The root conftest redirects it
# so unit tests cannot announce themselves as the machine's Fun Time; here the
# opposite is needed — the guard below has to read the very file the user's session
# publishes, which is the only thing that can tell this run to stop.
os.environ.pop(CLAIM_PATH_ENV_VAR, None)


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
def _abort_if_fun_time_opens():
    """Stop the run the moment the user opens Fun Time underneath it.

    ``allow_integration_run`` answers once, before this process even exists, so
    it can only ever see a session that was already up.  A run lasts minutes;
    nothing stopped the user opening Fun Time during one, and then two sessions
    shared the machine — which is how a run came to be running alongside a live
    session in the first place.

    ``interrupt_main`` is what makes the abort a clean one: it raises
    KeyboardInterrupt in pytest's main thread, and pytest answers that by tearing
    down every fixture still on the stack.  So the session's children die by
    recorded identity, the temp tree goes, and the machine-wide lock is released
    — the same unwinding a finished run does, just early.  A kill from outside
    would leave all of that behind.
    """
    stop = threading.Event()
    threading.Thread(
        target=watch_for_live_session,
        kwargs={"abort": _thread.interrupt_main, "stop": stop},
        name="live-session-watch",
        daemon=True,
    ).start()
    try:
        yield
    finally:
        stop.set()


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
