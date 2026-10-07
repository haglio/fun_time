from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from app_support.threading_utils import start_daemon_thread
from player_core.file_channel import append_command
from player_core.player_verbs import RELOAD_PLAYLIST, SET_F_MODE, play_file

from .modes import (
    PLAYLIST_MAIN_PLAYER,
    VideoShapes,
    build_main_playlist_paths,
    build_playlist_file_path,
    scripted_item,
    write_playlist_file,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MainListBuild:
    scripted_filter: bool
    main_sources: str
    state_dir: Path
    main_player_cmd_file: Path
    recent: bool = False
    start_at_top: bool = False
    shapes: VideoShapes | None = None
    metadata_root: Path | None = None

    def run(self) -> None:
        paths = build_main_playlist_paths(
            self.main_sources, self.scripted_filter, recent=self.recent, shapes=self.shapes,
            metadata_root=self.metadata_root)
        write_playlist_file(
            build_playlist_file_path(Path(self.state_dir), PLAYLIST_MAIN_PLAYER), paths,
            metadata_root=self.metadata_root)
        verbs = [RELOAD_PLAYLIST, f"{SET_F_MODE} {int(self.scripted_filter)}"]
        if self.start_at_top and paths:
            verbs.append(play_file(scripted_item(paths[0], self.metadata_root)))
        for verb in verbs:
            append_command(Path(self.main_player_cmd_file), verb)

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
                logger.exception("The main player's list could not be rebuilt")
