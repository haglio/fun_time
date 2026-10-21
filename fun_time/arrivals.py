"""What comes into a library while a session watches it, oldest first, until taken."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .folder_listings import FolderListings
from .media_metadata import normalize_path_key

logger = logging.getLogger(__name__)

LOOK_EVERY_S = 2.0


def keep_looking(look: Callable[[], None], stop: threading.Event, *,
                 every_s: float = LOOK_EVERY_S) -> None:
    while True:
        try:
            look()
        except Exception:
            logger.exception("Could not look for what has arrived in the library")
        if stop.wait(every_s):
            return


class Lineup(Protocol):
    name: str

    def in_latest(self) -> bool: ...

    def plays_its_own_list(self) -> bool: ...

    def lets_through(self, paths: list[str]) -> list[str]: ...

    def put_up_next(self, path: str) -> None: ...


class Arrivals:
    def __init__(self, list_library: Callable[[], list[str]]) -> None:
        self._list_library = list_library
        self._lock = threading.Lock()
        self._seen: set[str] | None = None
        self._waiting: list[str] = []

    def look(self) -> None:
        listed = {normalize_path_key(path): path for path in self._list_library()}
        if not listed:
            return
        with self._lock:
            if self._seen is None:
                self._seen = set()
            else:
                new = [path for key, path in listed.items() if key not in self._seen]
                still_there = [path for path in self._waiting if normalize_path_key(path) in listed]
                self._waiting = still_there + sorted(new, key=FolderListings().modified)
            self._seen |= set(listed)

    def waiting(self) -> list[str]:
        with self._lock:
            return list(self._waiting)

    def take(self, paths: list[str]) -> None:
        taken = {normalize_path_key(path) for path in paths}
        with self._lock:
            self._waiting = [path for path in self._waiting
                             if normalize_path_key(path) not in taken]

    def hand_to(self, lineup: Lineup) -> None:
        waiting = self.waiting()
        if not waiting:
            return
        if not lineup.in_latest():
            self.take(waiting)
        elif lineup.plays_its_own_list():
            for path in lineup.lets_through(waiting):
                logger.info("Up next on %s, as it arrived: %s", lineup.name, Path(path).name)
                lineup.put_up_next(path)
            self.take(waiting)
