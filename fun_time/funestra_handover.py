from __future__ import annotations

from collections.abc import Callable, Collection
from pathlib import Path
from typing import TYPE_CHECKING

from funestra.file_channel import append_command
from funestra.funestra_verbs import LOCK_OFF, LOCK_ON, RELOAD_PLAYLIST
from funestra.playlist import read_playlist, write_playlist
from funestra.satellite_hud import parse_hud

from .bridge_records import IN_ORIGENERATOR_MODE, BridgeConfig, SatelliteChannel, WindowOp, ignored
from .fun_time_funestras import FunTimeFunestra
from .hosted_contract import ENHANCED_ONLY, said_to_the_hosted_app
from .mode_plan import EnhancementLevel
from .modes import rotated_onto
from .origenerator_switches import origenerator_shows
from .satellite_control import read_satellite_status

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


def _slideshows_switch(channel: SatelliteChannel, switch: str) -> bool | None:
    if channel.origenerator_hud_file is None:
        return None
    try:
        hud = parse_hud(channel.origenerator_hud_file.read_text(encoding="utf-8"))
    except OSError:
        return None
    if hud is None:
        return None
    return next((button.lit for row in hud.rows for button in row
                 if button.command == f"{hud.funestra}_{switch}"), None)


def show_f_mode(channel: SatelliteChannel) -> bool | None:
    return _slideshows_switch(channel, "fmode")


def show_enhanced_filter(channel: SatelliteChannel) -> bool | None:
    return _slideshows_switch(channel, "enhanced")


def slideshows_enhancement(
    funestra: FunTimeFunestra, level: EnhancementLevel, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    if level is EnhancementLevel.ORIGINAL:
        return ignored(state, IN_ORIGENERATOR_MODE)
    if level is EnhancementLevel.ENHANCED:
        append_command(config.origenerator_cmd_file,
                       said_to_the_hosted_app(funestra.label, ENHANCED_ONLY))
    elif show_enhanced_filter(config.satellite(funestra)):
        append_command(config.origenerator_cmd_file, f"{funestra.label}_enhanced")
    return state, []


def taken_since(channel: SatelliteChannel, stamp: HudStamp) -> bool:
    if hud_stamp(channel) == stamp:
        return False
    try:
        return bool(channel.origenerator_hud_file.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def let_go_since(channel: SatelliteChannel, stamp: HudStamp) -> bool:
    if hud_stamp(channel) == stamp:
        return False
    try:
        return not channel.origenerator_hud_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _kept(playlist_file: Path) -> Path:
    return playlist_file.with_name(f"{playlist_file.stem}.kept.tsv")


def keep_aside(channel: SatelliteChannel) -> None:
    kept = _kept(channel.playlist_file)
    if kept.exists():
        return
    entries = read_playlist(channel.playlist_file)
    if not entries:
        return
    video = read_satellite_status(channel.status_file).video
    write_playlist(kept, rotated_onto(entries, video))


def take_back_the_list(playlist_file: Path, *, onto: str = "") -> bool:
    kept = _kept(playlist_file)
    entries = read_playlist(kept)
    if not entries:
        return False
    write_playlist(playlist_file, rotated_onto(entries, onto) if onto else entries)
    kept.unlink(missing_ok=True)
    return True


def hand_back(channel: SatelliteChannel, *, onto: str = "") -> bool:
    if not take_back_the_list(channel.playlist_file, onto=onto):
        return False
    append_command(channel.cmd_file, RELOAD_PLAYLIST)
    return True


LET_GO_TIMEOUT_S = 3.0


class Departures:
    def __init__(self) -> None:
        self._going: dict[FunTimeFunestra, HudStamp] = {}

    def expect(self, funestras: tuple[FunTimeFunestra, ...], config: BridgeConfig) -> None:
        for funestra in funestras:
            self._going[funestra] = hud_stamp(config.satellite(funestra))

    def forget(self, funestra: FunTimeFunestra) -> None:
        self._going.pop(funestra, None)

    def taken(self, config: BridgeConfig) -> list[FunTimeFunestra]:
        taken = [funestra for funestra, stamp in self._going.items()
                 if taken_since(config.satellite(funestra), stamp)]
        for funestra in taken:
            del self._going[funestra]
        return taken


class Homecoming:
    def __init__(self) -> None:
        self._coming: dict[FunTimeFunestra, tuple[float, HudStamp]] = {}

    @property
    def funestras(self) -> Collection[FunTimeFunestra]:
        return self._coming.keys()

    def expect(self, funestras: tuple[FunTimeFunestra, ...], config: BridgeConfig, *,
               now: float) -> None:
        for funestra in funestras:
            self._coming[funestra] = (
                now + LET_GO_TIMEOUT_S, hud_stamp(config.satellite(funestra)))

    def bring(self, state: BridgeState, config: BridgeConfig, *, now: float,
              left_on: Callable[[FunTimeFunestra], str]) -> list[FunTimeFunestra]:
        landed = []
        for funestra, (deadline, stamp) in list(self._coming.items()):
            if origenerator_shows(state.satellite(funestra).mode):
                del self._coming[funestra]
                continue
            channel = config.satellite(funestra)
            if now < deadline and not let_go_since(channel, stamp):
                continue
            del self._coming[funestra]
            hand_back(channel, onto=left_on(funestra))
            append_command(channel.cmd_file,
                           LOCK_ON if state.satellite(funestra).locked else LOCK_OFF)
            landed.append(funestra)
        return landed


class SatellitesChangingHands:
    def __init__(self, departures: Departures, homecoming: Homecoming, *,
                 left_on: Callable[[FunTimeFunestra], str],
                 changed: Callable[[BridgeState], None]) -> None:
        self._departures = departures
        self._homecoming = homecoming
        self._left_on = left_on
        self._changed = changed

    def follow(self, state: BridgeState, config: BridgeConfig, *, now: float) -> BridgeState:
        for funestra in self._departures.taken(config):
            state = self._hand(state, funestra, to_the_app=True)
        for funestra in self._homecoming.bring(state, config, now=now, left_on=self._left_on):
            self._departures.forget(funestra)
            state = self._hand(state, funestra, to_the_app=False)
        return state

    def _hand(self, state: BridgeState, funestra: FunTimeFunestra, *,
              to_the_app: bool) -> BridgeState:
        state = state.with_satellite(funestra, handed_over=to_the_app)
        self._changed(state)
        return state
