from __future__ import annotations

from pathlib import Path

from funestra.main_hud import OSR2_DRIVING, MainHudModel, read_main_hud
from funestra.main_hud_painter import MainHud
from funestra.modes import MainMode, Osr2State
from funestra.osr2_part_painter import Osr2PartPainter
from funestra.osr2_visualizer import DriveHud, read_drive
from funestra.robot_hand import FULL_INTENSITY, WaveformShape

from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.osr2_section import Osr2PartFacts, osr2_part, osr2_part_path, osr2_readout_path
from fun_time.read_when_changed import ReadWhenChanged


def room_for_a_whole_part() -> tuple[int, int]:
    whole = osr2_part(FunTimeFunestra.MAIN, Osr2PartFacts(
        main_mode=MainMode.KINO, osr2=Osr2State.ROBOT_HAND, control=OSR2_DRIVING, paused=False,
        learned=False, shape=WaveformShape.SINE.value, broker=True, max_intensity=FULL_INTENSITY,
    ))
    return Osr2PartPainter().rgba(MainHud(main_hud_model=whole, drive=DriveHud()))[1]


class Osr2PartReader:
    def __init__(self, state_dir: Path) -> None:
        self._state_dir = Path(state_dir)
        self._part: MainHudModel | None = None
        self._readout: DriveHud | None = None
        self._part_file = ReadWhenChanged(osr2_part_path(self._state_dir), read_main_hud)
        self._readout_files: dict[str, ReadWhenChanged[DriveHud | None]] = {}

    def read(self) -> MainHud | None:
        part = self._part_file.latest() or self._part
        if part is None:
            return None
        same_holder = self._part is not None and part.funestra == self._part.funestra
        self._readout = (self._readout_of(part.funestra).latest()
                         or (self._readout if same_holder else None))
        self._part = part
        return MainHud(main_hud_model=part, drive=self._readout)

    def _readout_of(self, holder: str) -> ReadWhenChanged[DriveHud | None]:
        reader = self._readout_files.get(holder)
        if reader is None:
            reader = self._readout_files[holder] = ReadWhenChanged(
                osr2_readout_path(self._state_dir, holder), read_drive)
        return reader
