from __future__ import annotations

import os
import time
from pathlib import Path


def heard_from_again_once_stale(progress_file: Path, *, stale_after_s: float,
                                within_s: float = 5.0) -> bool:
    long_ago = time.time() - stale_after_s - 1
    os.utime(progress_file, (long_ago, long_ago))
    deadline = time.monotonic() + within_s
    while progress_file.stat().st_mtime <= long_ago and time.monotonic() < deadline:
        time.sleep(0.05)
    return progress_file.stat().st_mtime > long_ago
