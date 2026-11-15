from __future__ import annotations

from collections.abc import Callable, Mapping

from funestra.modes import MainMode

from .bridge_records import BridgeConfig
from .fun_time_funestras import FunTimeFunestra
from .funestra_status import read_genau_status, read_main_funestra_status
from .hud_transport import the_hosted_slideshow_is_locked
from .satellite_control import read_satellite_status

Holding = Callable[[], tuple[bool, str]]


def what_the_funestras_hold(config: BridgeConfig) -> dict[str, Holding]:
    def kino() -> tuple[bool, str]:
        status = read_main_funestra_status(config.main_funestra.status_file)
        return status.locked, status.video

    def genau() -> tuple[bool, str]:
        status = read_genau_status(config.genau.status_file)
        return status.locked, status.flick

    def satellite(funestra: FunTimeFunestra) -> Holding:
        files = config.satellite(funestra)

        def held() -> tuple[bool, str]:
            status = read_satellite_status(files.status_file)
            return (status.locked and the_hosted_slideshow_is_locked(files.origenerator_hud_file),
                    status.video)
        return held

    return {MainMode.KINO: kino, MainMode.GENAU: genau,
            **{funestra.label: satellite(funestra) for funestra in FunTimeFunestra.SATELLITES}}


class LockFollower:
    def __init__(self, holders: Mapping[str, Holding]) -> None:
        self._holders = holders
        self._awaiting_the_lock_of: str | None = None

    def follow(self, holder: str) -> None:
        self._awaiting_the_lock_of = holder

    def held(self) -> tuple[str, str] | None:
        holder = self._awaiting_the_lock_of
        if holder is None:
            return None
        locked, item = self._holders[holder]()
        if not (locked and item):
            return None
        self._awaiting_the_lock_of = None
        return holder, item
