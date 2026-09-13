from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from player_core.file_channel import append_command
from player_core.player_verbs import SET_SPEED

from .player_status import read_main_player_status


class SatelliteSpeeds:
    def __init__(self, *, main_player_status_file: Path, satellite_cmd_files: Sequence[Path]) -> None:
        self._main_player_status_file = main_player_status_file
        self._satellite_cmd_files = tuple(satellite_cmd_files)
        self._main_rate: float | None = None

    def take_the_main_players_rate(self) -> None:
        rate = read_main_player_status(self._main_player_status_file).speed
        if rate is None or rate == self._main_rate:
            return
        first_read = self._main_rate is None
        self._main_rate = rate
        if first_read:
            return
        for cmd_file in self._satellite_cmd_files:
            append_command(cmd_file, f"{SET_SPEED} {rate:g}")
