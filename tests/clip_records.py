from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClipRecord:
    compilation: str
    index: int
    source: str
    performer: str
    full_video: str = ""
    scene_offset: float | None = None

    def recorded(self) -> dict:
        recorded: dict = {"compilation": self.compilation, "index": self.index,
                          "source": self.source, "performer": self.performer}
        if self.full_video:
            recorded["full_video"] = self.full_video
        if self.scene_offset is not None:
            recorded["scene_offset"] = self.scene_offset
        return recorded
