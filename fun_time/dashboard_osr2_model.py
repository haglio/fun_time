from __future__ import annotations

from pathlib import Path

from funestra.console import OSR2_DRIVING, ConsoleModel, read_console
from funestra.console_hud import ConsoleHud, ConsolePainter
from funestra.drive_readout import DriveHud, read_drive
from funestra.modes import MainMode, Osr2State
from funestra.robot_hand import FULL_INTENSITY, WaveformShape

from fun_time.osr2_section import osr2_part, osr2_part_path, osr2_readout_path
from fun_time.room_funestras import RoomFunestra


def room_for_a_whole_part() -> tuple[int, int]:
    whole = osr2_part(RoomFunestra.MAIN, main_mode=MainMode.KINO, osr2=Osr2State.ROBOT_HAND,
                      control=OSR2_DRIVING, paused=False, learned=False,
                      shape=WaveformShape.SINE.value, broker=True, max_intensity=FULL_INTENSITY)
    return ConsolePainter(device_only=True).rgba(ConsoleHud(console=whole, drive=DriveHud()))[1]


class Osr2PartReader:
    def __init__(self, state_dir: Path) -> None:
        self._state_dir = Path(state_dir)
        self._part: ConsoleModel | None = None
        self._readout: DriveHud | None = None

    def read(self) -> ConsoleHud | None:
        part = read_console(osr2_part_path(self._state_dir)) or self._part
        if part is None:
            return None
        same_holder = self._part is not None and part.funestra == self._part.funestra
        self._readout = (read_drive(osr2_readout_path(self._state_dir, part.funestra))
                         or (self._readout if same_holder else None))
        self._part = part
        return ConsoleHud(console=part, drive=self._readout)
