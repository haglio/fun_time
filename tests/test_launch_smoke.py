"""The launch's import chain, run the way the launcher runs it.

A verification session starts as ``venv python -m fun_time.orchestrator`` with
this checkout as the working directory and no inherited ``PYTHONPATH`` — so
every cross-repo import resolves through the venv's editable installs, which
name the primary checkouts.  The unit suite cannot see a break there: it runs
with the sibling branches injected onto ``PYTHONPATH``, which is exactly the
help the real launch does not get.  A branch that grew a dependency on an
unlanded sibling change therefore went green everywhere and died on his
double-click (2026-08-13: the hybrid arbiter importing player_core's new
floor-touch rule).  This imports the orchestrator the launcher's way, so that
break turns red here first.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent


def test_the_orchestrator_imports_the_way_the_launcher_runs_it():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", "import fun_time.orchestrator"],
        cwd=str(REPO_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
