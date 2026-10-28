from __future__ import annotations

from pathlib import Path

from funestra.console import OSR2_DRIVING, ConsoleModel, read_console
from funestra.console_hud import ConsoleHud, ConsolePainter
from funestra.device_panel import DevicePanel
from funestra.drive_readout import DriveHud, read_drive
from funestra.modes import MainMode, Osr2State
from funestra.robot_hand import FULL_INTENSITY, WaveformShape
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QWidget
from shared_ui.pressable_picture import PressablePicture

from fun_time.osr2_section import osr2_part, osr2_part_path, osr2_readout_path
from fun_time.room_funestras import RoomFunestra

READ_EVERY_MS = 100
NOTHING_DRAWN = (bytes(4), (1, 1))


def room_for_a_whole_part() -> tuple[int, int]:
    whole = osr2_part(RoomFunestra.MAIN, main_mode=MainMode.KINO, osr2=Osr2State.ROBOT_HAND,
                      control=OSR2_DRIVING, paused=False, learned=False,
                      shape=WaveformShape.SINE.value, broker=True, max_intensity=FULL_INTENSITY)
    return ConsolePainter(device_only=True).rgba(ConsoleHud(console=whole, drive=DriveHud()))[1]


class Osr2PartWidget(PressablePicture):
    action_triggered = pyqtSignal(str)

    def __init__(self, state_dir: Path, parent: QWidget | None = None) -> None:
        panel = DevicePanel(post=self._post)
        super().__init__(panel, parent)
        self._panel = panel
        self._state_dir = Path(state_dir)
        self._part: ConsoleModel | None = None
        self._readout: DriveHud | None = None
        self.setFixedSize(*room_for_a_whole_part())
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(READ_EVERY_MS)

    def picture(self) -> tuple[bytes, tuple[int, int]]:
        if self._part is None:
            return NOTHING_DRAWN
        return self._panel.picture(ConsoleHud(console=self._part, drive=self._readout))

    def poll(self) -> None:
        part = read_console(osr2_part_path(self._state_dir)) or self._part
        if part is None:
            return
        same_holder = self._part is not None and part.funestra == self._part.funestra
        self._readout = (read_drive(osr2_readout_path(self._state_dir, part.funestra))
                         or (self._readout if same_holder else None))
        self._part = part
        self.update()

    def shutdown(self) -> None:
        self._timer.stop()

    def _post(self, command: str) -> None:
        self.action_triggered.emit(command)
