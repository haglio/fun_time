"""Every Fun Time process records its own crash.

The orchestrators and the audio companion have always installed the family's
exception hooks; the three windowed children — the dashboard, a satellite and
the headset player — set up bare ``basicConfig`` and nothing else, so a crash
on one of their worker threads reached the parent's capture as an unlabeled
traceback and nothing said which process or which thread it came out of.
"""
from __future__ import annotations

import importlib
import inspect
import sys
import threading
from types import SimpleNamespace

import pytest

ENTRY_POINTS = (
    "fun_time.dashboard_app",
    "fun_time.audio_companion_app",
    "fun_time.orchestrator",
    "fun_time_vr.orchestrator",
    "fun_time_vr.player",
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


@pytest.mark.parametrize("module_name", ENTRY_POINTS)
def test_every_entry_points_logging_step_runs(module_name, hooks_restored, tmp_path):
    """Being callable is not running: the headset player named a helper it never
    imported, so its very first line raised NameError and every Enter VR closed
    Fun Time with nothing in the headset (2026-09-20).  The processes with a log
    of their own are handed where it goes; the windowed ones take nothing."""
    try:
        module = importlib.import_module(module_name)
    except ImportError as missing:  # pragma: no cover - engine-dependent
        pytest.skip(f"{module_name} needs {missing.name}")
    config = SimpleNamespace(log_file=lambda name: tmp_path / f"{name}.log")

    takes_a_config = bool(inspect.signature(module.set_up_logging).parameters)
    logger = module.set_up_logging(config) if takes_a_config else module.set_up_logging()

    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


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
