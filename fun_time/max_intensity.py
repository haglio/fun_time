from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from funestra.drive_layout import MAX_INTENSITY
from funestra.file_channel import append_command
from funestra.funestra_verbs import SET_MAX_INTENSITY
from funestra.robot_hand import FULL_INTENSITY

from .bridge_records import BridgeConfig, Route, WindowOp
from .command_value import Command
from .event_log import SOURCE_MAIN
from .fun_time_funestras import FunTimeFunestra

if TYPE_CHECKING:
    from .shared_state import BridgeState



def funestras_that_can_drive_the_osr2(config) -> tuple[Path, ...]:
    return (config.genau_cmd_file, config.main_funestra_cmd_file,
            *(config.satellite(funestra).cmd_file for funestra in FunTimeFunestra.SATELLITES))


def publish_max_intensity(command_files: Iterable[Path], *, max_intensity: int) -> None:
    verb = f"{SET_MAX_INTENSITY} {max_intensity}"
    for command_file in command_files:
        append_command(command_file, verb)


def set_max_intensity(command: Command, state: BridgeState, config: BridgeConfig,
                      _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    max_intensity = max(0, min(FULL_INTENSITY, int(command.argument)))
    publish_max_intensity(funestras_that_can_drive_the_osr2(config), max_intensity=max_intensity)
    return (replace(state, max_intensity=max_intensity),
            [WindowOp(op="message", key=f"Max intensity {max_intensity}%", source=SOURCE_MAIN)])


VALUED: dict[str, Route] = {MAX_INTENSITY: set_max_intensity}
