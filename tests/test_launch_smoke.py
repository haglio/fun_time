from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from app_support.launcher import launchers

REPO_DIR = Path(__file__).resolve().parent.parent

_LAUNCH_MODULE = re.compile(r'-m\s+([A-Za-z_][\w.]*)')

_UNLAUNCHED_ENTRY_POINTS = (
    ("session_handoff (started by an orchestrator)", "fun_time.session_handoff"),
    ("windows_bridge_orchestrator (loaded under the loading screen)",
     "fun_time.windows_bridge_orchestrator"),
)


def _launched_modules() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = list(_UNLAUNCHED_ENTRY_POINTS)
    for launcher in launchers(REPO_DIR):
        found.extend((launcher.file, module) for module in _LAUNCH_MODULE.findall(launcher.run))
    return found


LAUNCHED = _launched_modules()


def test_the_launchers_name_modules_to_check():
    assert [module for launcher, module in LAUNCHED if launcher.endswith(".vbs")]


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
        check=False,
    )
    assert result.returncode == 0, f"{launcher} runs {module}:\n{result.stderr}"
