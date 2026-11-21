from __future__ import annotations

import os
from pathlib import Path

from app_support.written_file import STAMPED_TOO_LATELY_TO_TELL_NS


def left_to_stand(*paths: Path) -> None:
    for path in paths:
        stood = path.stat().st_mtime_ns - 10 * STAMPED_TOO_LATELY_TO_TELL_NS
        os.utime(path, ns=(stood, stood))
