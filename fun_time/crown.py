from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from player_core.modes import MainMode

from .mode_plan import main_player_displays
from .player_status import read_main_player_status

if TYPE_CHECKING:
    from .shared_state import BridgeState

CROWN_ICON = "\U0001F451"


class Crown(StrEnum):
    MAIN = "main"
    PORTRAIT = "portrait"

    @property
    def command(self) -> str:
        return f"{self}_crown"


CROWNS = {crown.command: crown for crown in Crown}


def majority(crowned: Crown, *, main_mode: MainMode, main_portrait: bool | None) -> Crown:
    main_takes_it = crowned is Crown.MAIN and main_player_displays(main_mode) and main_portrait
    return Crown.MAIN if main_takes_it else Crown.PORTRAIT


def majority_now(state: BridgeState, main_player_status_file: Path) -> Crown:
    return majority(state.crowned, main_mode=state.main_mode,
                    main_portrait=read_main_player_status(main_player_status_file).portrait)
