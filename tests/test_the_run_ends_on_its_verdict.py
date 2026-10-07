from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_A_LIBRARY_THAT_DIES_ON_THE_WAY_OUT = """
import atexit, os

def test_passes():
    atexit.register(os._exit, 7)
"""


def test_a_run_ends_on_pytests_verdict_whatever_a_library_does_on_the_way_out(tmp_path: Path):
    test_file = tmp_path / "test_dies_on_the_way_out.py"
    test_file.write_text(_A_LIBRARY_THAT_DIES_ON_THE_WAY_OUT, encoding="utf-8")

    run = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_file), "-p", "tests.conftest", "-q",
         "-o", "addopts=-p no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
        env={**os.environ, "PYTEST_ADDOPTS": ""},
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    assert "1 passed" in run.stdout, run.stdout + run.stderr
