from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowseCallbacks:
    on_pick: Callable[[str], None]
    on_close: Callable[[], None] | None = None
    on_dismiss: Callable[[], None] | None = None


@dataclass(frozen=True)
class BrowseOpening:
    over: tuple[int, int, int, int] | None = None
    playing: str | None = None
    flicks: bool = False
