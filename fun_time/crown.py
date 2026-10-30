from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from funestra.hud_marks import shared_mark

from .mode_plan import kino_shows

if TYPE_CHECKING:
    from .bridge_records import BridgeConfig, WindowOp
    from .role_windows import WindowRoles
    from .shared_state import BridgeState
    from .window_layout import SecondaryMonitorRects

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


class Seating:
    """Where the Main and Portrait Funestras sit on the secondary monitor: by
    the crown, else by the shape of what the main slot shows."""

    def __init__(self, rects: Callable[..., SecondaryMonitorRects] | None, *,
                 main_funestra_status_file: Path, genau_status_file: Path) -> None:
        self._rects = rects
        self._main_funestra_status_file = main_funestra_status_file
        self._genau_status_file = genau_status_file

    def seat(self, state: BridgeState, windows: WindowRoles) -> BridgeState:
        if self._rects is None:
            return state
        most = majority_now(state, self._main_funestra_status_file, self._genau_status_file)
        windows.seat(self._rects(majority=most))
        return state if most is state.majority else replace(state, majority=most)


def crowned(crown: Crown, state: BridgeState, _config: BridgeConfig,
           _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return replace(state, crowned=crown), []


HANDLERS = {command: partial(crowned, crown) for command, crown in CROWNS.items()}
