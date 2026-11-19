from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

_NOT_READ = object()


class ReadWhenChanged[T]:
    def __init__(self, path: Path, read: Callable[[Path], T]) -> None:
        self._path = path
        self._read = read
        self._stamp: object = _NOT_READ
        self._value: T | None = None

    def latest(self) -> T:
        stamp = _stamp_of(self._path)
        if stamp != self._stamp:
            self._value, self._stamp = self._read(self._path), stamp
        return self._value


def _stamp_of(path: Path) -> tuple[int, int, int] | None:
    try:
        written = os.stat(path)
    except OSError:
        return None
    return written.st_mtime_ns, written.st_size, written.st_ino
