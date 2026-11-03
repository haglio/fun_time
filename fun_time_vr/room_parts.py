from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RoomUnits:
    main_unit: Any
    satellites: Sequence[Any]
    dash: Any
    main_hud_unit: Any
    reference: Any
    library: Any
    flashed: Any
    gallery: Any
    loading_screen: Any

    def in_order(self) -> list:
        return [self.main_unit, *self.satellites, self.dash, self.main_hud_unit, self.reference,
                self.library, self.flashed, self.gallery, self.loading_screen]


@dataclass(frozen=True)
class RoomKeeping:
    messages: Any
    keeper: Any
    posts: Any
