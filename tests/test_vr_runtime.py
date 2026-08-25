"""Tests for fun_time_vr.vr_runtime — what ``probe()`` answers when VR does not.

``probe()`` is what FunTimeVR asks before it launches anything, and its whole
job is to turn a failure into a ``Probe`` the popup can read.  These are the
ways it used to raise one instead.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fun_time_vr.vr_runtime import Readiness, probe


@contextmanager
def _loader(stub):
    """Stand *stub* in for the OpenXR loader ``probe()`` imports for itself."""
    with patch.dict(sys.modules, {"xr": stub}):
        yield


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
