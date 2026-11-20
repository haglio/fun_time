from __future__ import annotations

from pathlib import Path

from funestra.hud_button import Button
from funestra.satellite_hud import HudModel, parse_hud

from .bridge_records import BridgeConfig
from .config import SatelliteChannel
from .fun_time_funestras import FunTimeFunestra
from .polled_files import FileStamp, file_stamp
from .read_when_changed import ReadWhenChanged

HudStamp = FileStamp | None


def hud_stamp(channel: SatelliteChannel) -> HudStamp:
    return file_stamp(channel.origenerator_hud_file)


def _parsed_hud(path: Path) -> HudModel | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return parse_hud(text)


_PUBLISHED_HUDS: dict[Path, ReadWhenChanged[HudModel | None]] = {}


def published_hud(path: Path) -> HudModel | None:
    reader = _PUBLISHED_HUDS.get(path)
    if reader is None:
        reader = _PUBLISHED_HUDS[path] = ReadWhenChanged(path, _parsed_hud)
    return reader.latest()


def _slideshows_button(channel: SatelliteChannel, verb: str) -> Button | None:
    hud = published_hud(channel.origenerator_hud_file)
    if hud is None:
        return None
    return next((button for row in hud.rows for button in row
                 if button.command == f"{hud.funestra}_{verb}"), None)


def shows_button_lit(channel: SatelliteChannel, verb: str) -> bool | None:
    button = _slideshows_button(channel, verb)
    return None if button is None else button.lit


def show_has_something_to_reset(channel: SatelliteChannel) -> bool:
    button = _slideshows_button(channel, "reset")
    return button is not None and not button.dim


def _the_app_has_published_nothing_since(channel: SatelliteChannel, stamp: HudStamp) -> bool:
    return hud_stamp(channel) == stamp


def taken_since(channel: SatelliteChannel, stamp: HudStamp) -> bool:
    if _the_app_has_published_nothing_since(channel, stamp):
        return False
    try:
        return bool(channel.origenerator_hud_file.read_text(encoding="utf-8").strip())
    except OSError:
        return False


class HandOvers:
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
