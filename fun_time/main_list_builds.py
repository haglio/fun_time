from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from app_support.threading_utils import start_daemon_thread
from funestra.file_channel import append_command
from funestra.funestra_verbs import RELOAD_PLAYLIST, SET_F_MODE, play_file

from .main_list import MainList, MainListSettings
from .modes import scripted_item

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MainListBuild:
    main_list: MainList
    settings: MainListSettings
    main_funestra_cmd_file: Path
    start_at_top: bool = False

    def run(self) -> None:
        videos = self.main_list.write(self.settings)
        verbs = [RELOAD_PLAYLIST, f"{SET_F_MODE} {int(self.settings.scripted_filter)}"]
        if self.start_at_top and videos:
            verbs.append(play_file(scripted_item(videos[0], self.main_list.metadata_root)))
        for verb in verbs:
            append_command(Path(self.main_funestra_cmd_file), verb)

    def replacing(self, waiting: MainListBuild) -> MainListBuild:
        return replace(self, start_at_top=self.start_at_top or waiting.start_at_top)


class MainListBuilds(Protocol):
    """Where a :class:`MainListBuild` runs -- synchronously in tests
    (:class:`BuildsHere`), off the dispatch thread in a real bridge session
    (:class:`BuildsOffTheLoop`), so a slow drive cannot freeze it."""

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
        """Block until every build asked for so far has actually run."""
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
