from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .polled_files import file_stamp

_NOT_READ = object()


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
