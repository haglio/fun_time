from __future__ import annotations

import importlib
import inspect
import logging
import sys
import threading
from types import ModuleType, SimpleNamespace

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

THE_ONE_NAME_A_NEW_ENTRY_POINT_COPIES = "set_up_logging"


@pytest.fixture
def hooks_restored():
    was = (sys.excepthook, threading.excepthook)
    yield
    sys.excepthook, threading.excepthook = was


def _entry_point(module_name: str) -> ModuleType:
    try:
        return importlib.import_module(module_name)
    except ImportError as missing:  # pragma: no loading screen - engine-dependent
        pytest.skip(f"{module_name} needs {missing.name}")


def _run_its_logging_step(module: ModuleType, tmp_path) -> None:
    step = getattr(module, THE_ONE_NAME_A_NEW_ENTRY_POINT_COPIES)
    config = SimpleNamespace(log_file=lambda name: tmp_path / f"{name}.log")
    takes_a_config = bool(inspect.signature(step).parameters)
    logger: logging.Logger = step(config) if takes_a_config else step()

    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


@pytest.mark.parametrize("module_name", ENTRY_POINTS)
def test_every_entry_point_has_the_one_logging_step_a_new_one_copies(
        module_name, hooks_restored):
    assert callable(getattr(_entry_point(module_name), THE_ONE_NAME_A_NEW_ENTRY_POINT_COPIES))


@pytest.mark.parametrize("module_name", ENTRY_POINTS)
def test_each_logging_step_is_run_not_only_found(
        module_name, hooks_restored, tmp_path):
    _run_its_logging_step(_entry_point(module_name), tmp_path)


@pytest.mark.parametrize("module_name", ENTRY_POINTS)
def test_no_entry_point_leaves_a_crash_to_pythons_unlabeled_traceback(
        module_name, hooks_restored, tmp_path):
    before = (sys.excepthook, threading.excepthook)

    _run_its_logging_step(_entry_point(module_name), tmp_path)

    assert sys.excepthook is not before[0]
    assert threading.excepthook is not before[1]


def test_a_worker_threads_crash_is_logged_critical_under_the_threads_name(
        hooks_restored, caplog):
    logger = set_up_logging()
    with caplog.at_level("CRITICAL", logger=logger.name):
        thread = threading.Thread(target=lambda: 1 / 0, name="example-worker")
        thread.start()
        thread.join()

    assert "example-worker" in caplog.text
    assert "ZeroDivisionError" in caplog.text
