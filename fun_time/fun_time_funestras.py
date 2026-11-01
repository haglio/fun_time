from __future__ import annotations

from enum import IntEnum


class FunTimeFunestra(IntEnum):
    MAIN = 1
    PORTRAIT = 2
    LANDSCAPE = 3

    @property
    def label(self) -> str:
        return self.name.lower()

    @classmethod
    def for_scope(cls, scope: str) -> tuple[FunTimeFunestra, ...]:
        if scope == "satellites":
            return cls.SATELLITES
        return (cls.PORTRAIT,) if scope == "portrait" else (cls.LANDSCAPE,)


FunTimeFunestra.SATELLITES = (FunTimeFunestra.PORTRAIT, FunTimeFunestra.LANDSCAPE)
