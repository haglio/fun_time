"""The room's Funestras playing their Latest lists, each handed what arrives in its
library to play next."""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from funestra.file_channel import append_command
from funestra.funestra_verbs import play_next

from .arrivals import Arrivals
from .bridge_records import BridgeConfig
from .command_dispatch import hosts
from .funestra_status import read_main_funestra_status
from .main_list import MainListSettings
from .media_metadata import learn_indexed_clip
from .mode_plan import kino_shows
from .modes import collect_video_files, satellite_narrowing, scripted_item
from .room_funestras import RoomFunestra
from .shared_state import BridgeState


@dataclass(frozen=True)
class _TheMainFunestra:
    config: BridgeConfig
    state: BridgeState
    name: str = "the Main Funestra"

    def in_latest(self) -> bool:
        return self.state.main_latest

    def plays_its_own_list(self) -> bool:
        return (kino_shows(self.state.main_mode)
                and not read_main_funestra_status(self.config.main_funestra_status_file).compilation)

    def lets_through(self, paths: list[str]) -> list[str]:
        return self.config.main_list.lets_through(paths, MainListSettings.of(self.state))

    def put_up_next(self, path: str) -> None:
        append_command(self.config.main_funestra_cmd_file,
                       play_next(scripted_item(path, self.config.regen_metadata_root)))


@dataclass(frozen=True)
class _ASatellite:
    config: BridgeConfig
    state: BridgeState
    funestra: RoomFunestra
    coming_home: Collection[RoomFunestra]

    @property
    def name(self) -> str:
        return f"the {self.funestra.label.capitalize()} Funestra"

    def in_latest(self) -> bool:
        return self.state.satellite(self.funestra).latest

    def plays_its_own_list(self) -> bool:
        return not (self.state.satellite(self.funestra).loop or self.funestra in self.coming_home
                    or hosts(self.funestra, self.state, self.config))

    def lets_through(self, paths: list[str]) -> list[str]:
        side = self.state.satellite(self.funestra)
        return satellite_narrowing(paths, side.favorites_filter, self.config.favs_file,
                                   filter_query=side.filter,
                                   metadata_root=self.config.regen_metadata_root)

    def put_up_next(self, path: str) -> None:
        channel = self.config.satellite(self.funestra)
        learn_indexed_clip(channel.sources, path, self.config.regen_metadata_root)
        append_command(channel.cmd_file,
                       play_next(scripted_item(path, self.config.regen_metadata_root)))


class LatestArrivals:
    def __init__(self, config: BridgeConfig) -> None:
        self._config = config
        self._main = Arrivals(lambda: collect_video_files(config.main_sources))
        self._satellites = {
            funestra: Arrivals(lambda funestra=funestra: collect_video_files(
                config.satellite(funestra).sources))
            for funestra in RoomFunestra.SATELLITES}

    def look(self) -> None:
        for arrivals in (self._main, *self._satellites.values()):
            arrivals.look()

    def deliver(self, state: BridgeState, *,
                coming_home: Collection[RoomFunestra] = ()) -> None:
        self._main.hand_to(_TheMainFunestra(self._config, state))
        for funestra, arrivals in self._satellites.items():
            arrivals.hand_to(_ASatellite(self._config, state, funestra, coming_home))
