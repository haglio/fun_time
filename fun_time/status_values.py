from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from app_support.file_channel import read_key_values
from app_support.written_file import ReadEachWhenChanged

WRITTEN_IN_THE_SAME_TICK_NS = 32_000_000
_STATUS_FILES_KEPT = 32


class StatusValues:
    def __init__(self, *, read: Callable[[Path], dict[str, str]] = read_key_values,
                 clock: Callable[[], float] = time.time) -> None:
        self._files: ReadEachWhenChanged[Path, dict[str, str]] = ReadEachWhenChanged(
            read, most=_STATUS_FILES_KEPT, too_lately_ns=WRITTEN_IN_THE_SAME_TICK_NS,
            now_ns=lambda: round(clock() * 1_000_000_000))

    def read(self, path: Path) -> dict[str, str]:
        values = self._files.latest(Path(path))
        if values is None:
            raise FileNotFoundError(f"{path} has not been read yet")
        return dict(values)


STATUS_VALUES = StatusValues()


def read_status_values(path: Path) -> dict[str, str]:
    return STATUS_VALUES.read(path)
