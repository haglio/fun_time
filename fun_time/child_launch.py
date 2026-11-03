from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import IO

CHILD_LOG_MAX_BYTES = 1_000_000

_LAUNCH_MARK = "===== "


def no_child_log() -> dict:
    return {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def no_console_window() -> dict:
    if sys.platform != "win32":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def open_child_log(
    log_file: str | Path, argv: Sequence[str], *, max_bytes: int = CHILD_LOG_MAX_BYTES,
) -> IO[bytes]:
    path = Path(log_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _roll_oversize_log(path, max_bytes)
        handle = path.open("ab")
    except OSError:
        return open(os.devnull, "ab")  # noqa: SIM115 - the caller owns the handle
    flashed = f"{_LAUNCH_MARK}{time.strftime('%Y-%m-%d %H:%M:%S')} launch: {' '.join(str(a) for a in argv)}\n"
    handle.write(flashed.encode("utf-8", errors="replace"))
    handle.flush()
    return handle


def marks_a_launch(line: str) -> bool:
    return line.startswith(_LAUNCH_MARK)


def _roll_oversize_log(path: Path, max_bytes: int) -> None:
    # A stranded child still holding the old handle makes Windows refuse the rename.
    try:
        if path.stat().st_size <= max_bytes:
            return
        path.replace(path.with_name(path.name + ".1"))
    except OSError:
        pass
