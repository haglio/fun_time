"""Ensure no dead code accumulates in the production packages."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_no_dead_code():
    result = subprocess.run(
        [
            sys.executable, "-m", "vulture",
            str(ROOT / "fun_time"),
            # The satellite player ships from this repo too, so it is held to the
            # same bar. It went unscanned while it lived in genau, which is how
            # two unreachable SatelliteSession methods survived the move here.
            str(ROOT / "satellite"),
            str(ROOT / "fun_time_vr"),
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
