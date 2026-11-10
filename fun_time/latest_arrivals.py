from __future__ import annotations

from dataclasses import dataclass

from funestra.file_channel import append_command
from funestra.funestra_verbs import play_next

from .arrivals import Arrivals
from .bridge_records import BridgeConfig
from .funestra_status import read_main_funestra_status
from .main_list import MainListSettings
from .mode_plan import kino_shows
from .modes import collect_video_files, scripted_item
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


class LatestArrivals:
    def __init__(self, config: BridgeConfig) -> None:
        self._config = config
        self._main = Arrivals(lambda: collect_video_files(config.main_sources))

    def look(self) -> None:
        self._main.look()

    def deliver(self, state: BridgeState) -> None:
        self._main.hand_to(_TheMainFunestra(self._config, state))
