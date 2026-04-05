"""Ensure no dead code accumulates in the production package."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_no_dead_code():
    result = subprocess.run(
        [
            sys.executable, "-m", "vulture",
            str(ROOT / "fun_time"),
            str(ROOT / "vulture_whitelist.py"),
            "--min-confidence", "60",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"vulture found dead code:\n{result.stdout}{result.stderr}"
        )
