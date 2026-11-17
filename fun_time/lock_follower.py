from __future__ import annotations

from collections.abc import Callable, Mapping

from funestra.modes import MainMode

from .bridge_records import BridgeConfig
from .funestra_status import read_genau_status, read_main_funestra_status

Holding = Callable[[], tuple[bool, str]]


def what_the_main_funestra_holds(config: BridgeConfig) -> dict[MainMode, Holding]:
    def kino() -> tuple[bool, str]:
        status = read_main_funestra_status(config.main_funestra.status_file)
        return status.locked, status.video

    def genau() -> tuple[bool, str]:
        status = read_genau_status(config.genau.status_file)
        return status.locked, status.flick

    return {MainMode.KINO: kino, MainMode.GENAU: genau}


LOCK_WHAT_THEY_SHOW_AT_THE_PRESS = frozenset({MainMode.KINO})


class LockFollower:
    def __init__(self, holders: Mapping[str, Holding]) -> None:
        self._holders = holders
        self._awaiting_the_lock_of: str | None = None
        self._core_went_ahead_to = ""

    def follow(self, holder: str) -> str:
        self._awaiting_the_lock_of = holder
        self._core_went_ahead_to = ""
        return self._holders[holder]()[1] if holder in LOCK_WHAT_THEY_SHOW_AT_THE_PRESS else ""

    def core_went_ahead_to(self, item: str) -> None:
        self._core_went_ahead_to = item

    def core_is_at(self, item: str) -> bool:
        return bool(item) and item == self._core_went_ahead_to

    def held(self) -> str:
        if self._awaiting_the_lock_of is None:
            return ""
        locked, item = self._holders[self._awaiting_the_lock_of]()
        if not (locked and item):
            return ""
        self._awaiting_the_lock_of = None
        return item
