"""The three Funestras, as one identity.

The dispatcher counts Funestras in slots (1=main, 2=portrait, 3=landscape);
everything that *draws* them names them.  An IntEnum so ``active_funestra`` keeps
its INI wire value (``str(Funestra.PORTRAIT)`` is ``"2"``) and int-keyed tables
keep working, with :attr:`label` as the one crossing to the drawing names."""
from __future__ import annotations

from enum import IntEnum


class RoomFunestra(IntEnum):
    MAIN = 1
    PORTRAIT = 2
    LANDSCAPE = 3

    @property
    def label(self) -> str:
        """The name the drawing side uses for this Funestra."""
        return self.name.lower()

    @classmethod
    def label_of(cls, slot: int) -> str:
        """:attr:`label` for *slot*, or "" for a slot no Funestra holds — a
        hand-edited state file can carry any int, and the HUD keeps drawing."""
        try:
            return cls(slot).label
        except ValueError:
            return ""

    @classmethod
    def for_scope(cls, scope: str) -> tuple[RoomFunestra, ...]:
        """The Funestras a both/portrait/landscape scope word names."""
        if scope == "satellites":
            return cls.SATELLITES
        return (cls.PORTRAIT,) if scope == "portrait" else (cls.LANDSCAPE,)


# The scope "satellites" reaches — the two satellites, never the Main Funestra.
RoomFunestra.SATELLITES = (RoomFunestra.PORTRAIT, RoomFunestra.LANDSCAPE)
