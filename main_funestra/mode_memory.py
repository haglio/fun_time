from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from funestra.file_channel import publish_whole


@dataclass(frozen=True)
class RememberedMode:
    compilation: str = ""
    video: str = ""


class ModeMemory:
    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._written: RememberedMode | None = None

    def read(self) -> RememberedMode:
        fields = self._fields()
        self._written = RememberedMode(
            compilation=fields.get("compilation", ""),
            video=fields.get("video", ""),
        )
        return self._written

    def sync(self, mode: RememberedMode) -> None:
        if mode != self._written:
            self.write(mode)

    def write(self, mode: RememberedMode) -> None:
        self._written = mode
        if self._path is None:
            return
        publish_whole(self._path, f"compilation={mode.compilation}\nvideo={mode.video}\n")

    def _fields(self) -> dict[str, str]:
        if self._path is None:
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        return dict(
            line.split("=", 1) for line in text.splitlines() if "=" in line
        )
