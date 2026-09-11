"""What the room around this player says, kept between frames.

Two files arrive published by somebody else: the console panel Fun Time writes
about the main slot, and the readout Genau writes about what it is doing to the
device.  Both are replaced while this polls them, so a lost race reads a torn
file — and the readers answer None for that, meaning *keep what you have*.
Blanking either for a frame is a visible flicker on a HUD that redraws at 60fps.

Lived as two locals rebound inside ``main_player.app``'s run loop, so the rule that keeps
them — the ``or`` on each read — had no test.
"""
from __future__ import annotations

from pathlib import Path

from player_core.console import ConsoleModel, read_console
from player_core.drive_readout import DriveHud, read_drive


class Published:
    """The last whole console and motion this player managed to read."""

    def __init__(self, console_file: Path, drive_file: Path) -> None:
        self._console_file = console_file
        self._drive_file = drive_file
        self._console = ConsoleModel()
        self._drive: DriveHud | None = None

    @property
    def console(self) -> ConsoleModel:
        """What Fun Time last said about the main slot."""
        return self._console

    @property
    def drive(self) -> DriveHud | None:
        """Genau's own readout as it last published it, or None while nothing
        has published one yet."""
        return self._drive

    def refresh(self) -> None:
        """Read this frame's files, keeping whatever comes back torn."""
        self._console = read_console(self._console_file) or self._console
        self._drive = read_drive(self._drive_file) or self._drive
