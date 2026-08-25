"""Tests for fun_time_vr.vr_runtime — what ``probe()`` answers when VR does not.

``probe()`` is what FunTimeVR asks before it launches anything, and its whole
job is to turn a failure into a ``Probe`` the popup can read.  These are the
ways it used to raise one instead.
"""
from __future__ import annotations

import builtins
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from fun_time_vr.vr_runtime import Readiness, probe


@contextmanager
def _loader(stub):
    """Stand *stub* in for the OpenXR loader ``probe()`` imports for itself."""
    with patch.dict(sys.modules, {"xr": stub}):
        yield


@contextmanager
def _a_loader_that_will_not_load(exc):
    """Fail the import itself, the way a loader that cannot load does.

    Not an ``ImportError``: pyopenxr raises ``NotImplementedError`` off Windows,
    and ``LoadLibrary`` raises ``OSError`` on a Windows box whose loader DLL is
    unusable.  Neither ever happens on the machine the gate runs on, which is
    why this has to be staged rather than waited for.
    """
    real_import = builtins.__import__

    def _import(name, *args, **kwargs):
        if name == "xr":
            raise exc
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", _import):
        yield


@pytest.mark.parametrize(
    "exc",
    [NotImplementedError("this platform has no OpenXR loader"),
     OSError("the loader DLL could not be loaded")],
    ids=["off_windows", "unusable_loader_dll"],
)
def test_a_loader_that_will_not_load_answers_rather_than_raises(exc):
    """The popup reads a Probe; an exception here reached it as a stack trace."""
    with _a_loader_that_will_not_load(exc):
        result = probe()

    assert result.readiness is Readiness.FAILED
    assert str(exc) in result.detail


def test_probe_survives_a_teardown_that_fails_under_it():
    """Releasing the instance cannot replace the answer probe() decided on.

    ``ensure_ready()`` polls ``probe()`` and every loader failure has to reach
    the popup; an exception raised on the way out escapes past both.  A bare
    ``MagicMock`` is loader enough here: this case reaches no ``except`` clause,
    because the answer is settled before the teardown runs.
    """
    stub = MagicMock()
    stub.destroy_instance.side_effect = RuntimeError("the runtime shut down mid-probe")

    with _loader(stub):
        result = probe()

    assert result.readiness is Readiness.READY
    stub.destroy_instance.assert_called_once()


def test_a_teardown_that_failed_is_written_down(caplog):
    """Swallowed and unrecorded, it would be the silent exit this app avoids."""
    stub = MagicMock()
    stub.destroy_instance.side_effect = RuntimeError("the runtime shut down mid-probe")

    with _loader(stub), caplog.at_level("WARNING"):
        probe()

    assert "the runtime shut down mid-probe" in caplog.text
