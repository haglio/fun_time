from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

THE_CHECKOUT_A_LAUNCHER_STARTS_IN = Path(__file__).resolve().parent.parent


def without_the_pythonpath_no_launcher_sets(environment: Mapping[str, str]) -> dict[str, str]:
    return {name: value for name, value in environment.items() if name != "PYTHONPATH"}


def run_python_the_way_a_launcher_does(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(THE_CHECKOUT_A_LAUNCHER_STARTS_IN),
        env=without_the_pythonpath_no_launcher_sets(os.environ),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
