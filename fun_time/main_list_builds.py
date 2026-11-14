from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from app_support.threading_utils import start_daemon_thread
from funestra.file_channel import append_command
from funestra.funestra_verbs import LOCK_ON, RELOAD_PLAYLIST, play_file
from funestra.inclusion_pair import answer_to_a_narrowing

from .event_log import MESSAGE, SOURCE_MAIN, flash_message
from .main_list import MainList, MainListSettings
from .modes import scripted_item

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MainListBuild:
    main_list: MainList
    settings: MainListSettings
    main_funestra_cmd_file: Path
    on_screen: str = ""
    start_at_top: bool = False
    says: str = ""

    def run(self) -> None:
        written = self.main_list.write(self.settings, on_screen=self.on_screen,
                                       from_the_top=self.start_at_top)
        verbs = [RELOAD_PLAYLIST, *self.settings.flags_no_playlist_can_show]
        answer = answer_to_a_narrowing(self.says, nothing_left=written.nothing_left,
                                       things="videos")
        if answer.holds_what_is_on_screen:
            verbs.append(LOCK_ON)
        elif self.start_at_top:
            verbs.append(self._jump_onto_the_list_just_reloaded(written.videos[0]))
        for verb in verbs:
            append_command(Path(self.main_funestra_cmd_file), verb)
        if answer.says:
            flash_message(logger, answer.says, source=SOURCE_MAIN,
                          level=logging.WARNING if answer.warns else MESSAGE)

    def _jump_onto_the_list_just_reloaded(self, head: str) -> str:
        with_the_script_the_osr2_is_driven_from = scripted_item(head, self.main_list.metadata_root)
        return play_file(with_the_script_the_osr2_is_driven_from)

    def replacing(self, waiting: MainListBuild) -> MainListBuild:
        return replace(self, start_at_top=self.start_at_top or waiting.start_at_top)


class MainListBuilds(Protocol):
    def build(self, build: MainListBuild) -> None: ...

    def settle(self) -> None: ...


class BuildsHere:
    def build(self, build: MainListBuild) -> None:
        build.run()

    def settle(self) -> None:
        pass


BUILDS_HERE = BuildsHere()


class BuildsOffTheLoop:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting: MainListBuild | None = None
        self._idle = threading.Event()
        self._idle.set()

    def build(self, build: MainListBuild) -> None:
        with self._lock:
            self._waiting = build if self._waiting is None else build.replacing(self._waiting)
            if self._idle.is_set():
                self._idle.clear()
                start_daemon_thread(target=self._build_until_none_wait, name="main-list-builds")

    def settle(self) -> None:
        self._idle.wait()

    def _build_until_none_wait(self) -> None:
        while True:
            with self._lock:
                build, self._waiting = self._waiting, None
                if build is None:
                    self._idle.set()
                    return
            try:
                build.run()
            except Exception:
                logger.exception("The Main Funestra's list could not be rebuilt")
