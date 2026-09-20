"""Every Fun Time process records its own crash.

The orchestrators and the audio companion have always installed the family's
exception hooks; the three windowed children — the dashboard, a satellite and
the headset player — set up bare ``basicConfig`` and nothing else, so a crash
on one of their worker threads reached the parent's capture as an unlabeled
traceback and nothing said which process or which thread it came out of.
"""
from __future__ import annotations

import importlib
import sys
import threading

import pytest

ENTRY_POINTS = (
    "fun_time.dashboard_app",
    "fun_time.audio_companion_app",
    "fun_time.orchestrator",
    "fun_time_vr.orchestrator",
    "satellite.app",
)


@pytest.fixture
def hooks_restored():
    """Both hooks are process-wide; put the run's own back afterwards."""
    was = (sys.excepthook, threading.excepthook)
    yield
    sys.excepthook, threading.excepthook = was


@pytest.mark.parametrize("module_name", ENTRY_POINTS)
def test_every_entry_point_can_install_the_family_hooks(module_name, hooks_restored):
    """The two players import a video engine this machine may not have; what is
    pinned here is that each module exposes the same setup step, called the same
    thing, so a sixth entry point has one obvious thing to copy."""
    try:
        module = importlib.import_module(module_name)
    except ImportError as missing:  # pragma: no cover - engine-dependent
        pytest.skip(f"{module_name} needs {missing.name}")

    assert callable(module.set_up_logging)


def test_a_crash_on_a_worker_thread_names_the_thread(hooks_restored, caplog):
    """The default thread hook prints a traceback with no logger and no level,
    so a satellite that died on its own thread left the parent's log looking
    like output rather than like a failure."""
    from satellite.app import set_up_logging

    logger = set_up_logging()
    with caplog.at_level("CRITICAL", logger=logger.name):
        thread = threading.Thread(target=lambda: 1 / 0, name="example-worker")
        thread.start()
        thread.join()

    assert "example-worker" in caplog.text
    assert "ZeroDivisionError" in caplog.text
