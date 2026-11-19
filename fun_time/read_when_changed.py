from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

_NOT_READ = object()

FileStamp = tuple[int, int, int]


class ReadWhenChanged[T]:
    def __init__(self, path: Path, read: Callable[[Path], T]) -> None:
        self._path = path
        self._read = read
        self._stamp: object = _NOT_READ
        self._value: T | None = None

    def latest(self) -> T:
        stamp = file_stamp(self._path)
        if stamp != self._stamp:
            try:
                self._value, self._stamp = self._read(self._path), stamp
            except OSError:
                return self._value
        return self._value


def file_stamp(path: Path) -> FileStamp | None:
    try:
        written = os.stat(path)
    except OSError:
        return None
    return written.st_mtime_ns, written.st_size, written.st_ino
