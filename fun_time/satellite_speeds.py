from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from funestra_core.file_channel import append_command
from funestra_core.funestra_verbs import SET_SPEED

from .funestra_status import MainFunestraStatus, read_main_funestra_status


class SatelliteSpeeds:
    def __init__(self, *, main_funestra_status_file: Path, satellite_cmd_files: Sequence[Path]) -> None:
        self._main_funestra_status_file = main_funestra_status_file
        self._satellite_cmd_files = tuple(satellite_cmd_files)
        self._main_funestra: MainFunestraStatus | None = None

    def take_the_main_funestras_rate(self) -> None:
        last = self._main_funestra
        self._main_funestra = read_main_funestra_status(self._main_funestra_status_file, fallback=last)
        if last is None or self._main_funestra.speed == last.speed:
            return
        for cmd_file in self._satellite_cmd_files:
            append_command(cmd_file, f"{SET_SPEED} {self._main_funestra.speed:g}")
