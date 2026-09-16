from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from tests.integration import flake_gate_install
from tests.integration.flake_gate_install import FLAKE_GATE, flake_gate_python


def _made(state_dir: Path, requirement: str) -> Path:
    venv = state_dir / "flake_gate_venv"
    python = venv / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (venv / "installed.txt").write_text(requirement, encoding="utf-8")
    return python


def test_the_first_repeat_makes_the_install_it_runs_from(tmp_path: Path):
    with patch.object(flake_gate_install.subprocess, "run") as run:
        python = flake_gate_python(tmp_path)

    venv = tmp_path / "flake_gate_venv"
    assert python == venv / "Scripts" / "python.exe"
    assert [call.args[0] for call in run.call_args_list] == [
        [sys._base_executable, "-m", "venv", "--clear", str(venv)],
        [str(python), "-m", "pip", "install", "--quiet", FLAKE_GATE],
    ]
    assert (venv / "installed.txt").read_text(encoding="utf-8") == FLAKE_GATE


def test_an_install_already_made_is_used_as_it_is(tmp_path: Path):
    made = _made(tmp_path, FLAKE_GATE)

    with patch.object(flake_gate_install.subprocess, "run") as run:
        assert flake_gate_python(tmp_path) == made

    run.assert_not_called()


def test_an_install_of_another_version_is_made_again(tmp_path: Path):
    _made(tmp_path, "app-support @ git+https://github.com/haglio/app_support@v0.0.1")

    with patch.object(flake_gate_install.subprocess, "run") as run:
        flake_gate_python(tmp_path)

    assert run.call_count == 2
    assert (tmp_path / "flake_gate_venv" / "installed.txt").read_text(encoding="utf-8") == FLAKE_GATE
