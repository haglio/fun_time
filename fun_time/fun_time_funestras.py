from __future__ import annotations

from enum import IntEnum


class FunTimeFunestra(IntEnum):
    MAIN = 1
    PORTRAIT = 2
    LANDSCAPE = 3

    @property
    def label(self) -> str:
        return self.name.lower()


FunTimeFunestra.SATELLITES = (FunTimeFunestra.PORTRAIT, FunTimeFunestra.LANDSCAPE)
