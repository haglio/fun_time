"""Every launcher's import chain, run the way that launcher runs it.

A session starts as ``venv python -m <module>`` with this checkout as the
working directory and no inherited ``PYTHONPATH`` — so every cross-repo import
resolves through the venv's editable installs, which name the primary
checkouts.  The unit suite cannot see a break there: it runs with the sibling
branches injected onto ``PYTHONPATH``, which is exactly the help the real launch
does not get.  A branch that grew a dependency on an unlanded sibling change
therefore went green everywhere and died on his double-click (2026-08-13: the
hybrid arbiter importing player_core's new floor-touch rule).

The modules are read out of the ``.vbs`` launchers rather than listed here, so
this covers whatever the checkout can actually be started as.  Listing them by
hand is what let the second one through: the fix went into
``fun_time.orchestrator``, the test named only that module, and
``fun_time_vr.orchestrator`` — a launcher of its own — stayed broken and green.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent

# ``... python.exe" -m fun_time.orchestrator ...`` inside the command each
# launcher builds for cmd.
_LAUNCH_MODULE = re.compile(r'-m\s+([A-Za-z_][\w.]*)')


def _launched_modules() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for launcher in sorted(REPO_DIR.glob("*.vbs")):
        text = launcher.read_text(encoding="utf-8", errors="replace")
        for module in dict.fromkeys(_LAUNCH_MODULE.findall(text)):
            found.append((launcher.name, module))
    return found


LAUNCHED = _launched_modules()


def test_the_launchers_name_modules_to_check():
    """A regex that matched nothing would make every case below vacuous."""
    assert LAUNCHED


@pytest.mark.parametrize(
    ("launcher", "module"),
    LAUNCHED,
    ids=[f"{launcher}-{module}" for launcher, module in LAUNCHED],
)
def test_a_launcher_s_module_imports_the_way_it_is_run(launcher, module):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=str(REPO_DIR),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"{launcher} runs {module}:\n{result.stderr}"
