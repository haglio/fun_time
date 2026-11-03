from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class BrowseCallbacks:
    on_pick: Callable[[str], None]
    on_close: Callable[[], None] | None = None
    on_dismiss: Callable[[], None] | None = None


GENAU_FLAG = "--genau"


@dataclass(frozen=True)
class BrowseOpening:
    over: tuple[int, int, int, int] | None = None
    playing: str | None = None
    flicks: bool = False

    def arguments(self) -> list[str]:
        arguments: list[str] = []
        if self.over is not None:
            x, y, width, height = self.over
            arguments += ["--x", str(x), "--y", str(y), "--width", str(width),
                          "--height", str(height)]
        if self.playing:
            arguments += ["--playing", self.playing]
        if self.flicks:
            arguments.append(GENAU_FLAG)
        return arguments
