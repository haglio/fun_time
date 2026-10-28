"""An OSR2 part and a readout made up for a test, and published where the
Dashboard reads them, on the desktop and in the headset alike."""
from __future__ import annotations

from pathlib import Path

from funestra.console import OSR2_DRIVING, ConsoleModel, console_text, read_console
from funestra.console_hud import ConsoleHud, ConsolePainter
from funestra.drive_readout import DriveHud, publish_drive, read_drive
from funestra.modes import MainMode, Osr2State

from fun_time.osr2_section import osr2_part, osr2_part_path, osr2_readout_path
from fun_time.room_funestras import RoomFunestra


def a_part(holder: RoomFunestra = RoomFunestra.MAIN, *, broker: bool = True) -> ConsoleModel:
    return osr2_part(holder, main_mode=MainMode.KINO, osr2=Osr2State.ROBOT_HAND,
                     control=OSR2_DRIVING, paused=False, learned=False, shape="sine",
                     broker=broker, max_intensity=100)


def a_readout(speed: int = 50) -> DriveHud:
    return DriveHud(speed=speed, amplitude=60, center=50, waveform=tuple(0.5 for _ in range(80)))


def published(state_dir: Path, part: ConsoleModel) -> ConsoleModel:
    """*part* written where the room publishes it, as it reads back."""
    path = osr2_part_path(state_dir)
    path.write_text(console_text(part), encoding="utf-8")
    return read_console(path)


def handed_on(state_dir: Path, holder: RoomFunestra, readout: DriveHud) -> DriveHud:
    """*readout* written where *holder* hands its readout on, as it reads back."""
    path = osr2_readout_path(state_dir, holder.label)
    publish_drive(path, readout)
    return read_drive(path)


def center_of(part: ConsoleModel, command: str) -> tuple[int, int]:
    """The middle of the button posting *command*, in the part's own pixels."""
    painter = ConsolePainter(device_only=True)
    painter.rgba(ConsoleHud(console=part))
    x, y, width, height = next(rect for rect, button in painter.buttons if button.command == command)
    return x + width // 2, y + height // 2


def band_of(part: ConsoleModel, readout: DriveHud, axis: str) -> tuple[int, int, int, int]:
    """Where the readout's band for *axis* is, in the part's own pixels."""
    painter = ConsolePainter(device_only=True)
    painter.rgba(ConsoleHud(console=part, drive=readout))
    return next(track.rect for track in painter.tracks if track.axis == axis)
