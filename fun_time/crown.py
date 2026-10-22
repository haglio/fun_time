from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from funestra.hud_marks import shared_mark

from .mode_plan import kino_shows

if TYPE_CHECKING:
    from .shared_state import BridgeState

CROWN_ICON = shared_mark("crown")


class Crown(StrEnum):
    MAIN = "main"
    PORTRAIT = "portrait"

    @property
    def command(self) -> str:
        return f"{self}_crown"


CROWNS = {crown.command: crown for crown in Crown}


def majority(crowned: Crown, *, main_portrait: bool | None, held: Crown) -> Crown:
    if crowned is Crown.PORTRAIT:
        return Crown.PORTRAIT
    if main_portrait is None:
        return held
    return Crown.MAIN if main_portrait else Crown.PORTRAIT


def majority_now(state: BridgeState, main_funestra_status_file: Path, genau_status_file: Path) -> Crown:
    from .funestra_status import read_genau_status, read_main_funestra_status  # noqa: PLC0415
    if kino_shows(state.main_mode):
        main_portrait = read_main_funestra_status(main_funestra_status_file).portrait
    else:
        main_portrait = read_genau_status(genau_status_file).portrait
    return majority(state.crowned, main_portrait=main_portrait, held=state.majority)
