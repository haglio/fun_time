"""The install the flake gate runs from: beside this repo's venv, not in it.

This venv pins the app_support the players run on, and the gate can live in a
newer one, so the gate gets a venv of its own under ``state/`` -- the same
arrangement the family merge gate uses on a runner.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

FLAKE_GATE = "app-support @ git+https://github.com/haglio/app_support@v0.1.165"


def flake_gate_python(state_dir: Path) -> Path:
    venv = state_dir / "flake_gate_venv"
    python = venv / "Scripts" / "python.exe"
    installed = venv / "installed.txt"
    if python.exists() and installed.exists() and installed.read_text(encoding="utf-8") == FLAKE_GATE:
        return python
    _run([sys._base_executable, "-m", "venv", "--clear", str(venv)])
    _run([str(python), "-m", "pip", "install", "--quiet", FLAKE_GATE])
    venv.mkdir(parents=True, exist_ok=True)
    installed.write_text(FLAKE_GATE, encoding="utf-8")
    return python


def _run(argv: list[str]) -> None:
    subprocess.run(argv, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
