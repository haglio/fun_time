from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from funestra.drive_layout import MAX_INTENSITY
from funestra.file_channel import append_command
from funestra.funestra_verbs import SET_MAX_INTENSITY

from .room_funestras import RoomFunestra

MAX_INTENSITY_COMMAND = f"{MAX_INTENSITY}_"


def funestras_that_can_drive_the_osr2(config) -> tuple[Path, ...]:
    return (config.genau_cmd_file, config.main_funestra_cmd_file,
            *(config.satellite(funestra).cmd_file for funestra in RoomFunestra.SATELLITES))


def publish_max_intensity(command_files: Iterable[Path], *, max_intensity: int) -> None:
    verb = f"{SET_MAX_INTENSITY} {max_intensity}"
    for command_file in command_files:
        append_command(command_file, verb)
