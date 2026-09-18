"""Closing a player must raise nothing its host would read as a crash.

libmpv raises Windows structured exceptions from its Lua engine, and a host that
has armed faulthandler -- pytest for this whole suite, and
``app_support.logging_utils.enable_faulthandler`` for Genau and Origenerator --
answers each one by dumping every thread's Python frames without the GIL.  mpv's
teardown is exactly when python-mpv's event thread is exiting, so the walk can
reach a thread state being freed and fault; nothing handles that, and the process
dies mid-dump.  It killed two hidden-desktop runs, and the second time it went
before pytest printed its summary, so an earlier failure in that run was never
reported at all.

Only reachable here: it takes the real DLL and a real GL context, which is why
player_core's own suite cannot ask this question.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from app_support.subprocess_utils import hidden_subprocess_kwargs

from tests.integration.integration_support import environment_with_this_checkouts_siblings

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32",
                       reason="Fun Time integration tests require Windows"),
    pytest.mark.skipif(os.environ.get("FUN_TIME_RUN_INTEGRATION") != "1",
                       reason="Set FUN_TIME_RUN_INTEGRATION=1 to run"),
]

# Enough for near-certainty without spending a minute: about a third of teardowns
# raised one while mpv's own Lua scripts were still being loaded (10 of 30
# measured), so thirty clean ones in a row do not happen by luck.
TEARDOWNS = 30

PROBE_TIMEOUT_S = 180.0


def test_closing_a_player_raises_nothing_its_host_reads_as_a_crash(tmp_path: Path):
    """The probe is a child because the regression is fatal: in this process it
    would take the run down before pytest could say which test was running."""
    log = tmp_path / "faulthandler.log"
    probe = subprocess.run(
        [sys.executable, "-m", "tests.integration.teardown_probe", str(log), str(TEARDOWNS)],
        cwd=Path(__file__).resolve().parents[2],
        env=environment_with_this_checkouts_siblings(),
        capture_output=True, text=True, timeout=PROBE_TIMEOUT_S,
        **hidden_subprocess_kwargs(),
    )
    caught = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""

    assert probe.returncode == 0, (
        f"opening and closing {TEARDOWNS} players ended the process "
        f"({probe.returncode}): {probe.stderr.strip() or caught.strip()}"
    )
    # An empty log is also what a probe that opened nothing would leave.
    assert probe.stdout.strip() == f"{TEARDOWNS} opened and closed", probe.stdout
    assert caught == "", (
        f"libmpv raised something faulthandler reported while {TEARDOWNS} players "
        f"were closing, and the next one to land while a thread is exiting takes "
        f"the process with it:\n{caught}"
    )
