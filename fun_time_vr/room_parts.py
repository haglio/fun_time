from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .funestras import (
        LoadingScreenUnit,
        _DashUnit,
        _GalleryUnit,
        _LibraryUnit,
        _MainHudUnit,
        _MainUnit,
        _MessageUnit,
        _ReferenceUnit,
    )


@dataclass(frozen=True)
class RoomUnits:
    main_unit: _MainUnit
    satellites: Sequence[Any]
    dash: _DashUnit
    main_hud_unit: _MainHudUnit
    reference: _ReferenceUnit
    library: _LibraryUnit
    flashed: _MessageUnit
    gallery: _GalleryUnit
    loading_screen: LoadingScreenUnit

    def in_order(self) -> list:
        return [self.main_unit, *self.satellites, self.dash, self.main_hud_unit, self.reference,
                self.library, self.flashed, self.gallery, self.loading_screen]


@dataclass(frozen=True)
class RoomKeeping:
    messages: Any
    keeper: Any
    posts: Any
