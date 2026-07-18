"""Where a launched child's stdout and stderr go.

All that is left of what was ``runtime_support``: the CLI, logging, threading and
subprocess scaffolding it held alongside this is now ``app_support``, and the
command-file reader it carried had no caller at all. This is the one piece that
is genuinely ours — it exists because *we* launch windowed children and have to
be able to diagnose one that dies.
"""
from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import IO

# A child's crash log is near-empty in normal use (a line per clip), so a
# megabyte spans days of sessions — matching the cap the app's own logs use.
CHILD_LOG_MAX_BYTES = 1_000_000


def open_child_log(
    log_file: str | Path, argv: Sequence[str], *, max_bytes: int = CHILD_LOG_MAX_BYTES,
) -> IO[bytes]:
    """Open *log_file* to receive a child process's stdout and stderr.

    A windowed child (``pythonw``) has no console, so without this its stderr goes
    nowhere: an unhandled exception kills it leaving no trace, which is what made
    the satellite deaths undiagnosable.  Handing the returned handle to
    ``Popen(stdout=…, stderr=…)`` gives the Python traceback *and* the native
    diagnostics libmpv writes to stderr a home on disk.

    Opened for append and stamped with a banner naming the launch time and argv,
    so a log spanning many sessions can be split by eye at the right one; a log
    that has grown past *max_bytes* is rolled aside first, so an every-day habit
    cannot grow one forever.
    """
    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    _roll_oversize_log(path, max_bytes)
    handle = path.open("ab")
    banner = f"===== {time.strftime('%Y-%m-%d %H:%M:%S')} launch: {' '.join(str(a) for a in argv)}\n"
    handle.write(banner.encode("utf-8", errors="replace"))
    handle.flush()
    return handle


def _roll_oversize_log(path: Path, max_bytes: int) -> None:
    """Move an oversize log aside to ``<name>.1``, keeping one generation.

    Best-effort: a stranded child still holding the old handle makes Windows
    refuse the rename, and log housekeeping must never keep a player from
    launching — the launch just appends to the big file instead.
    """
    try:
        if path.stat().st_size <= max_bytes:
            return
        path.replace(path.with_name(path.name + ".1"))
    except OSError:
        pass
