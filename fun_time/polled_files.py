from __future__ import annotations

import os
from pathlib import Path

from app_support.file_channel import publish_whole

READER_HOLD_BUDGET_S = 1.0
_RETRY_SPACING_S = 0.005

FileStamp = tuple[int, int, int]


def file_stamp(path: Path) -> FileStamp | None:
    try:
        written = os.stat(path)
    except OSError:
        return None
    return written.st_mtime_ns, written.st_size, written.st_ino


def replace_despite_readers(path: Path, text: str) -> bool:
    return publish_whole(path, text,
                         attempts=round(READER_HOLD_BUDGET_S / _RETRY_SPACING_S),
                         delay_s=_RETRY_SPACING_S)
