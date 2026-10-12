"""The compilation the Main Funestra was last in, kept across sessions.

Entering one swaps the Main Funestra's playlist *in memory only* — the list Fun
Time writes never holds it — so being inside a compilation is remembered here or
not at all.  The clip that was on screen comes too, because it is the anchor the
compilation is rebuilt around: Fun Time opens its list on the video its Funestra
last showed, but only when that video is *in* the list, and a compilation's
clips often are not.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from funestra.file_channel import publish_whole


@dataclass(frozen=True)
class RememberedMode:
    """What the Main Funestra was in when it last wrote itself down."""

    compilation: str = ""
    video: str = ""


class ModeMemory:
    """The Main Funestra's compilation in a small key=value file in its state dir."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        # What is already written down, so the Funestra can ask this every frame
        # without rewriting the same lines sixty times a second.  None until
        # something has been read or written: the file may not exist.
        self._written: RememberedMode | None = None

    def read(self) -> RememberedMode:
        fields = self._fields()
        self._written = RememberedMode(
            compilation=fields.get("compilation", ""),
            video=fields.get("video", ""),
        )
        return self._written

    def sync(self, mode: RememberedMode) -> None:
        """Write *mode* down if it is not what is down already.

        The mode moves on several paths -- a key, a command from Fun Time,
        leaving a compilation -- so there is no one moment to write it at, and
        the Funestra simply says this every frame.
        """
        if mode != self._written:
            self.write(mode)

    def write(self, mode: RememberedMode) -> None:
        """Remember *mode*; a write that cannot land is simply not remembered.

        Published whole: this is rewritten on any frame the mode moves and read
        back at the next startup, so a launch landing inside a write would read
        a truncated record and open on a mode nobody left it in.
        """
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
        # Split on the first "=" only: a compilation is titled for a shelf and
        # may carry anything but a newline.
        return dict(
            line.split("=", 1) for line in text.splitlines() if "=" in line
        )
