from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs

THE_GATES_OWN_APP_SUPPORT = "app-support @ git+https://github.com/haglio/app_support@v0.1.197"


def flake_gate_python(state_dir: Path) -> Path:
    venv = state_dir / "flake_gate_venv"
    python = venv / "Scripts" / "python.exe"
    installed = venv / "installed.txt"
    if python.exists() and installed.exists() and installed.read_text(encoding="utf-8") == THE_GATES_OWN_APP_SUPPORT:
        return python
    _run([sys._base_executable, "-m", "venv", "--clear", str(venv)])
    _run([str(python), "-m", "pip", "install", "--quiet", THE_GATES_OWN_APP_SUPPORT])
    venv.mkdir(parents=True, exist_ok=True)
    installed.write_text(THE_GATES_OWN_APP_SUPPORT, encoding="utf-8")
    return python


def _run(argv: list[str]) -> None:
    subprocess.run(argv, check=True, **hidden_subprocess_kwargs())
