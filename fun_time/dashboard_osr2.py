from __future__ import annotations

from pathlib import Path

from funestra.console_hud import ConsoleHud
from funestra.device_panel import DevicePanel
from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QWidget
from shared_ui.pressable_picture import PressablePicture

from fun_time.dashboard_osr2_model import Osr2PartReader, room_for_a_whole_part

READ_EVERY_MS = 100
NOTHING_DRAWN = (bytes(4), (1, 1))


class Osr2PartWidget(PressablePicture):
    action_triggered = pyqtSignal(str)

    def __init__(self, state_dir: Path, parent: QWidget | None = None) -> None:
        panel = DevicePanel(post=self._post)
        super().__init__(panel, parent)
        self._panel = panel
        self._reader = Osr2PartReader(state_dir)
        self._hud: ConsoleHud | None = None
        self.setFixedSize(*room_for_a_whole_part())
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        self._timer.start(READ_EVERY_MS)

    def picture(self) -> tuple[bytes, tuple[int, int]]:
        if self._hud is None:
            return NOTHING_DRAWN
        return self._panel.picture(self._hud)

    def poll(self) -> None:
        self._hud = self._reader.read()
        if self._hud is not None:
            self.update()

    def shutdown(self) -> None:
        self._timer.stop()

    def _post(self, command: str) -> None:
        self.action_triggered.emit(command)
