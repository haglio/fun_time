from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time.checkout_overrides import genau_project_kwargs

_LOCATE = (
    "from player_core import libmpv_loader as L;"
    "print(L.libmpv_dirs()[0] / 'libmpv-2.dll');"
    "print(L.machine_libmpv_dir() / 'libmpv-2.dll')"
)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def ensure_vendored(python_exe, project_dirs, *, run: Runner = subprocess.run) -> None:
    if not python_exe:
        return
    result = run(
        [str(python_exe), "-c", _LOCATE],
        capture_output=True,
        text=True,
        **genau_project_kwargs(project_dirs),
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return
    lines = result.stdout.strip().splitlines()
    if len(lines) != 2:
        return
    vendor_dll, machine_dll = Path(lines[0]), Path(lines[1])
    if vendor_dll.exists() or not machine_dll.exists():
        return
    vendor_dll.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(machine_dll, vendor_dll)


def ensure_engine_vendored(config, *, run: Runner = subprocess.run) -> None:
    ensure_vendored(config.paths.genau_python_exe, config.paths.genau_project_dirs, run=run)
    ensure_vendored(config.paths.python_exe, None, run=run)
