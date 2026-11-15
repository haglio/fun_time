from __future__ import annotations

from collections.abc import Iterable

from funestra.file_channel import append_command
from funestra.funestra_verbs import set_speed

from .config import FunestraChannel
from .funestra_status import MainFunestraStatus, read_main_funestra_status


class SatelliteSpeeds:
    def __init__(self, *, main_funestra: FunestraChannel,
                 satellites: Iterable[FunestraChannel]) -> None:
        self._main_funestra_status_file = main_funestra.status_file
        self._satellite_cmd_files = tuple(satellite.cmd_file for satellite in satellites)
        self._main_funestra: MainFunestraStatus | None = None

    def take_the_main_funestras_rate(self) -> None:
        last = self._main_funestra
        self._main_funestra = read_main_funestra_status(self._main_funestra_status_file, fallback=last)
        if last is None or self._main_funestra.speed == last.speed:
            return
        for cmd_file in self._satellite_cmd_files:
            append_command(cmd_file, set_speed(self._main_funestra.speed))
