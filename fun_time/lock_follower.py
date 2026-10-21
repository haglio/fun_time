from __future__ import annotations

from collections.abc import Callable, Mapping

from funestra.modes import MainMode

from .bridge_records import BridgeConfig
from .funestra_status import read_genau_status, read_main_funestra_status

Holding = Callable[[], tuple[bool, str]]


def what_the_main_slot_holds(config: BridgeConfig) -> dict[MainMode, Holding]:
    def kino() -> tuple[bool, str]:
        status = read_main_funestra_status(config.main_funestra_status_file)
        return status.locked, status.video

    def genau() -> tuple[bool, str]:
        status = read_genau_status(config.genau_status_file)
        return status.locked, status.flick

    return {MainMode.KINO: kino, MainMode.GENAU: genau}


class LockFollower:
    def __init__(self, holders: Mapping[str, Holding]) -> None:
        self._holders = holders
        self._following: str | None = None

    def follow(self, holder: str) -> None:
        self._following = holder

    def held(self) -> str:
        if self._following is None:
            return ""
        locked, item = self._holders[self._following]()
        if not (locked and item):
            return ""
        self._following = None
        return item
