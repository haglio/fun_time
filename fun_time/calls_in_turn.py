from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Hashable

logger = logging.getLogger(__name__)


class CallsInTurn:
    def __init__(self, thread_name: Callable[[Hashable], str]) -> None:
        self._thread_name = thread_name
        self._lock = threading.Lock()
        self._waiting: dict[Hashable, deque[tuple[Callable[[], object], threading.Event, str]]] = {}

    def make(self, key: Hashable, call: Callable[[], object], *, what: str) -> threading.Event:
        done = threading.Event()
        with self._lock:
            first = key not in self._waiting
            self._waiting.setdefault(key, deque()).append((call, done, what))
        if first:
            threading.Thread(target=self._make_in_turn, args=(key,), daemon=True,
                             name=self._thread_name(key)).start()
        return done

    def waiting(self, key: Hashable) -> int:
        with self._lock:
            return len(self._waiting.get(key, ()))

    def _make_in_turn(self, key: Hashable) -> None:
        finished = False
        while not finished:
            with self._lock:
                call, done, what = self._waiting[key][0]
            try:
                call()
            except Exception:
                logger.exception("%s failed", what)
            with self._lock:
                self._waiting[key].popleft()
                finished = not self._waiting[key]
                if finished:
                    del self._waiting[key]
            done.set()


class AtOnce:
    def make(self, _key: Hashable, call: Callable[[], object], *, what: str) -> threading.Event:
        call()
        done = threading.Event()
        done.set()
        return done

    def waiting(self, _key: Hashable) -> int:
        return 0
