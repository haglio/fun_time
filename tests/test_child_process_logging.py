from __future__ import annotations

import importlib
import inspect
import sys
import threading
from types import SimpleNamespace

import pytest

from satellite.app import set_up_logging

ENTRY_POINTS = (
    "fun_time.dashboard_app",
    "fun_time.audio_companion_app",
    "fun_time.orchestrator",
    "fun_time_vr.orchestrator",
    "fun_time_vr.app",
    "satellite.app",
)


@pytest.fixture
def hooks_restored():
    was = (sys.excepthook, threading.excepthook)
    yield
    sys.excepthook, threading.excepthook = was


@pytest.mark.parametrize("module_name", ENTRY_POINTS)
def test_every_entry_point_can_install_the_family_hooks(module_name, hooks_restored):
    try:
        module = importlib.import_module(module_name)
    except ImportError as missing:  # pragma: no loading screen - engine-dependent
        pytest.skip(f"{module_name} needs {missing.name}")

    assert callable(module.set_up_logging)


@pytest.mark.parametrize("module_name", ENTRY_POINTS)
def test_every_entry_points_logging_step_runs(module_name, hooks_restored, tmp_path):
    try:
        module = importlib.import_module(module_name)
    except ImportError as missing:  # pragma: no loading screen - engine-dependent
        pytest.skip(f"{module_name} needs {missing.name}")
    config = SimpleNamespace(log_file=lambda name: tmp_path / f"{name}.log")

    takes_a_config = bool(inspect.signature(module.set_up_logging).parameters)
    logger = module.set_up_logging(config) if takes_a_config else module.set_up_logging()

    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def test_a_crash_on_a_worker_thread_names_the_thread(hooks_restored, caplog):
    logger = set_up_logging()
    with caplog.at_level("CRITICAL", logger=logger.name):
        thread = threading.Thread(target=lambda: 1 / 0, name="example-worker")
        thread.start()
        thread.join()

    assert "example-worker" in caplog.text
    assert "ZeroDivisionError" in caplog.text
