from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from app_support.file_channel import read_key_values
from app_support.written_file import ReadWhenChanged

WRITTEN_IN_THE_SAME_TICK_S = 0.032


class StatusValues:
    def __init__(self, *, read: Callable[[Path], dict[str, str]] = read_key_values,
                 clock: Callable[[], float] = time.time) -> None:
        self._read = read
        self._clock = clock
        self._files: dict[Path, ReadWhenChanged[dict[str, str]]] = {}
        self._just_read: set[Path] = set()

    def read(self, path: Path) -> dict[str, str]:
        path = Path(path)
        file = self._files.get(path)
        if file is None:
            file = self._files[path] = ReadWhenChanged(path, self._read_noting_it)
        values = file.latest()
        if values is None:
            raise FileNotFoundError(f"{path} has not been read yet")
        if path in self._just_read:
            self._just_read.discard(path)
            if self._could_be_written_again_unseen(path):
                del self._files[path]
        return dict(values)

    def _read_noting_it(self, path: Path) -> dict[str, str]:
        values = self._read(path)
        self._just_read.add(path)
        return values

    def _could_be_written_again_unseen(self, path: Path) -> bool:
        try:
            written_at = os.stat(path).st_mtime
        except OSError:
            return False
        return self._clock() - written_at < WRITTEN_IN_THE_SAME_TICK_S


STATUS_VALUES = StatusValues()


def read_status_values(path: Path) -> dict[str, str]:
    return STATUS_VALUES.read(path)
