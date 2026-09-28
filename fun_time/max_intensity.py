from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from player_core.drive_layout import MAX_INTENSITY
from player_core.file_channel import append_command
from player_core.player_verbs import SET_MAX_INTENSITY

from .players import Player

MAX_INTENSITY_COMMAND = f"{MAX_INTENSITY}_"


def players_that_can_drive_the_osr2(config) -> tuple[Path, ...]:
    return (config.genau_cmd_file, config.main_player_cmd_file,
            *(config.satellite(player).cmd_file for player in Player.SATELLITES))


def publish_max_intensity(command_files: Iterable[Path], *, max_intensity: int) -> None:
    verb = f"{SET_MAX_INTENSITY} {max_intensity}"
    for command_file in command_files:
        append_command(command_file, verb)
