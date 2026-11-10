from __future__ import annotations

from typing import TYPE_CHECKING

from funestra.file_channel import append_command
from funestra.hud_button import Button
from funestra.satellite_hud import parse_hud

from .bridge_records import BridgeConfig, WindowOp
from .config import SatelliteChannel
from .fun_time_funestras import FunTimeFunestra
from .hosted_contract import SAID_FOR_AN_ENHANCEMENT_LEVEL, said_to_the_hosted_app
from .mode_plan import EnhancementLevel

if TYPE_CHECKING:
    from .shared_state import BridgeState

HudStamp = tuple[int, int] | None


def hud_stamp(channel: SatelliteChannel) -> HudStamp:
    if channel.origenerator_hud_file is None:
        return None
    try:
        stat = channel.origenerator_hud_file.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns


def _slideshows_button(channel: SatelliteChannel, verb: str) -> Button | None:
    if channel.origenerator_hud_file is None:
        return None
    try:
        hud = parse_hud(channel.origenerator_hud_file.read_text(encoding="utf-8"))
    except OSError:
        return None
    if hud is None:
        return None
    return next((button for row in hud.rows for button in row
                 if button.command == f"{hud.funestra}_{verb}"), None)


def show_f_mode(channel: SatelliteChannel) -> bool | None:
    button = _slideshows_button(channel, "fmode")
    return None if button is None else button.lit


def show_has_something_to_reset(channel: SatelliteChannel) -> bool:
    button = _slideshows_button(channel, "reset")
    return button is not None and not button.dim


def slideshows_enhancement(
    funestra: FunTimeFunestra, level: EnhancementLevel, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    append_command(config.origenerator_cmd_file, said_to_the_hosted_app(
        funestra.label, SAID_FOR_AN_ENHANCEMENT_LEVEL[level]))
    return state, []


def _the_app_has_published_nothing_since(channel: SatelliteChannel, stamp: HudStamp) -> bool:
    return hud_stamp(channel) == stamp


def taken_since(channel: SatelliteChannel, stamp: HudStamp) -> bool:
    if _the_app_has_published_nothing_since(channel, stamp):
        return False
    try:
        return bool(channel.origenerator_hud_file.read_text(encoding="utf-8").strip())
    except OSError:
        return False


class Departures:
    def __init__(self) -> None:
        self._going: dict[FunTimeFunestra, HudStamp] = {}

    def expect(self, funestras: tuple[FunTimeFunestra, ...], config: BridgeConfig) -> None:
        for funestra in funestras:
            self._going.setdefault(funestra, hud_stamp(config.satellite(funestra)))

    def taken(self, config: BridgeConfig) -> list[FunTimeFunestra]:
        taken = [funestra for funestra, stamp in self._going.items()
                 if taken_since(config.satellite(funestra), stamp)]
        for funestra in taken:
            del self._going[funestra]
        return taken

    def still_going(self) -> bool:
        return bool(self._going)
