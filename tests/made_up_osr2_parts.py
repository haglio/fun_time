from __future__ import annotations

from pathlib import Path

from funestra.main_hud import OSR2_DRIVING, MainHudModel, main_hud_text, read_main_hud
from funestra.main_hud_painter import MainHud, MainHudPainter
from funestra.modes import MainMode, Osr2State
from funestra.osr2_visualizer import DriveHud, publish_drive, read_drive

from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.osr2_section import osr2_part, osr2_part_path, osr2_readout_path


def a_part(holder: FunTimeFunestra = FunTimeFunestra.MAIN, *, broker: bool = True) -> MainHudModel:
    return osr2_part(holder, main_mode=MainMode.KINO, osr2=Osr2State.ROBOT_HAND,
                     control=OSR2_DRIVING, paused=False, learned=False, shape="sine",
                     broker=broker, max_intensity=100)


def a_readout(speed: int = 50) -> DriveHud:
    return DriveHud(speed=speed, amplitude=60, center=50, waveform=tuple(0.5 for _ in range(80)))


def published(state_dir: Path, part: MainHudModel) -> MainHudModel:
    path = osr2_part_path(state_dir)
    path.write_text(main_hud_text(part), encoding="utf-8")
    return read_main_hud(path)


def handed_on(state_dir: Path, holder: FunTimeFunestra, readout: DriveHud) -> DriveHud:
    path = osr2_readout_path(state_dir, holder.label)
    publish_drive(path, readout)
    return read_drive(path)


def center_of(part: MainHudModel, command: str) -> tuple[int, int]:
    painter = MainHudPainter(device_only=True)
    painter.rgba(MainHud(main_hud_model=part))
    x, y, width, height = next(rect for rect, button in painter.buttons if button.command == command)
    return x + width // 2, y + height // 2


def band_of(part: MainHudModel, readout: DriveHud, axis: str) -> tuple[int, int, int, int]:
    painter = MainHudPainter(device_only=True)
    painter.rgba(MainHud(main_hud_model=part, drive=readout))
    return next(track.rect for track in painter.tracks if track.axis == axis)
