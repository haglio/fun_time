from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence

from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time.process_identity import NAMER

_ENDING_S = 2.0


def start_helper(role: str, module: str, args: Sequence[str], *, python_exe: str,
                 launch: Callable[..., subprocess.Popen]) -> subprocess.Popen:
    return launch([NAMER.named_exe(python_exe, role), "-m", module, *args],
                  **hidden_subprocess_kwargs())


def end_helper(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=_ENDING_S)
    except subprocess.TimeoutExpired:
        process.kill()
