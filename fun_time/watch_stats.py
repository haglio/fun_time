from __future__ import annotations

import json
import time
from pathlib import Path

from app_support import video_ids
from app_support.kino_folder import is_ai_video
from funestra.video_groups import normalize_path_key

_EVENT_FIELDS = {
    "completion": "completions",
    "skip": "skips",
    "lock": "locks",
}


def watch_stats_path(state_dir: str | Path) -> Path:
    return Path(state_dir) / "watch_stats.json"


def load_watch_stats(stats_file: str | Path) -> dict[str, dict[str, int]]:
    try:
        with open(stats_file, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class WatchTracker:
    COMPLETE_FRACTION = 0.85
    SKIP_FRACTION = 0.60
    NAV_WINDOW_S = 3.0

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._path = ""
        self._max_fraction = 0.0
        self._last_nav_at = float("-inf")
        self._suppress_departed = False

    def note_user_nav(self) -> None:
        self._last_nav_at = self._clock()

    def note_discard(self) -> None:
        self._suppress_departed = True

    def observe(self, path: str, fraction: float) -> list[tuple[str, str]]:
        if path == self._path:
            if path and self._max_fraction >= self.COMPLETE_FRACTION and fraction < self._max_fraction - 0.5:
                self._max_fraction = max(0.0, fraction)
                return [("completion", path)]
            self._max_fraction = max(self._max_fraction, fraction)
            return []
        events = []
        if self._path and not self._suppress_departed:
            he_moved_on_himself = (self._clock() - self._last_nav_at) <= self.NAV_WINDOW_S
            if self._max_fraction >= self.COMPLETE_FRACTION:
                events.append(("completion", self._path))
            elif he_moved_on_himself and self._max_fraction <= self.SKIP_FRACTION:
                events.append(("skip", self._path))
        self._path = path
        self._max_fraction = max(0.0, fraction)
        self._suppress_departed = False
        return events


def _left_a_folder_still_there(key: str) -> bool:
    if not _is_a_path(key):
        return False
    video = Path(key)
    return not video.exists() and video.parent.exists()


def _is_a_path(key: str) -> bool:
    return Path(key).is_absolute()


def _key_of(video_path: str) -> str:
    if is_ai_video(video_path):
        return normalize_path_key(video_path)
    record = video_ids.record_of(Path(video_path)) if Path(video_path).is_file() else None
    return video_ids.id_of(record) if record is not None else normalize_path_key(video_path)


def _keyed_by_id(stats: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    keyed: dict[str, dict[str, int]] = {}
    for key, counts in stats.items():
        entry = keyed.setdefault(_key_of(key) if _is_a_path(key) else key,
                                 {"completions": 0, "skips": 0, "locks": 0})
        for field, count in counts.items():
            entry[field] = entry.get(field, 0) + count
    return keyed


def record_watch_event(stats_file: str | Path, video_path: str, event: str) -> None:
    field = _EVENT_FIELDS[event]
    stats = _keyed_by_id(load_watch_stats(stats_file))
    entry = stats.setdefault(_key_of(video_path), {"completions": 0, "skips": 0, "locks": 0})
    entry[field] = entry.get(field, 0) + 1
    stats = {key: value for key, value in stats.items() if not _left_a_folder_still_there(key)}
    target = Path(stats_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    tmp.replace(target)
