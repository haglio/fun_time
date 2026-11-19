from __future__ import annotations

import builtins
import io
import os
from collections import Counter
from pathlib import Path

_LOOKS = {
    os: ("stat", "scandir", "listdir"),
    os.path: ("exists", "isfile", "isdir"),
    builtins: ("open",),
    io: ("open",),
}


class DiskLooks:
    def __init__(self, monkeypatch, under: Path) -> None:
        self._under = os.path.normcase(os.path.abspath(under))
        self.made: Counter[str] = Counter()
        for module, names in _LOOKS.items():
            for name in names:
                monkeypatch.setattr(module, name, self._counting(name, getattr(module, name)))

    def _counting(self, name: str, look):
        def counted(path=".", *args, **kwargs):
            if isinstance(path, (str, os.PathLike)) and self._is_under(path):
                self.made[name] += 1
            return look(path, *args, **kwargs)
        return counted

    def _is_under(self, path: str | os.PathLike) -> bool:
        spelled = os.path.normcase(os.path.abspath(os.fspath(path)))
        return spelled == self._under or spelled.startswith(self._under + os.sep)

    @property
    def total(self) -> int:
        return sum(self.made.values())
