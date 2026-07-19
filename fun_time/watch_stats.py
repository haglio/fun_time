"""Per-video watch statistics driving playback frequency ("breeding").

Videos the user watches to the end (or locks and loops) accumulate positive
signals; videos skipped early accumulate negative ones.  The satellite
playlist builders convert the counts into weights, so loved videos surface
more often and chronically-skipped ones fade — a continuous companion to the
hard mark-as-weird removal.

Stats live in one JSON file under the state dir, keyed by normalized video
path: ``{"completions": int, "skips": int, "locks": int}``.
"""
from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path
from typing import Callable, Iterable

from .media_metadata import normalize_path_key

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


# score = completions + 3*locks - skips, softened by /3 and clamped so one
# video can be at most 8x more (or 8x less) frequent than a fresh one.
_LOCK_SCORE = 3.0
_SCORE_SOFTENING = 3.0
_MAX_DOUBLINGS = 3.0


def weight_for(entry: dict[str, int] | None) -> float:
    """Relative playback weight for one video's stats (1.0 when unknown)."""
    if not entry:
        return 1.0
    score = (
        entry.get("completions", 0)
        + _LOCK_SCORE * entry.get("locks", 0)
        - entry.get("skips", 0)
    )
    doublings = max(-_MAX_DOUBLINGS, min(_MAX_DOUBLINGS, score / _SCORE_SOFTENING))
    return float(2.0 ** doublings)


def weighted_shuffle(
    paths: Iterable[str],
    weight_of: Callable[[str], float | None],
    rng: random.Random,
) -> list[str]:
    """Shuffle with bias: heavier paths tend to land earlier.

    Efraimidis–Spirakis sampling — each path draws a key ``-log(u)/w`` and the
    list sorts ascending, which is a weighted draw without replacement.  With
    all weights equal it degenerates to a plain uniform shuffle.
    """

    def sort_key(path: str) -> float:
        weight = weight_of(path) or 1.0
        u = max(rng.random(), 1e-12)
        return -math.log(u) / weight

    return sorted(paths, key=sort_key)


def passes_inclusion(weight: float, rng: random.Random) -> bool:
    """Whether a video of *weight* makes this playlist build at all.

    Neutral-or-loved videos always play; disliked ones (weight < 1) sit out
    proportionally — the continuous version of mark-as-weird.
    """
    return weight >= 1.0 or rng.random() < weight


class WatchTracker:
    """Classify one player's playback into completions and skips.

    Player-agnostic: the same tracker serves a satellite and the primary Nau
    player.  Fed with periodic (path, position-fraction) samples plus
    notifications of user navigation and discards, it emits
    ("completion" | "skip", path) events: a video that reached ~the end counts
    as watched (each repeat-one wrap while locked counts again); a video the
    user navigated away from early counts as skipped; anything else — e.g. the
    automatic advance on unlock or discard — is neutral.
    """

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
            # A large backwards jump from ~the end is a repeat-one wrap
            # (satellites have no seek control): one full watch completed.
            if path and self._max_fraction >= self.COMPLETE_FRACTION and fraction < self._max_fraction - 0.5:
                self._max_fraction = max(0.0, fraction)
                return [("completion", path)]
            self._max_fraction = max(self._max_fraction, fraction)
            return []
        events = []
        if self._path and not self._suppress_departed:
            nav_recent = (self._clock() - self._last_nav_at) <= self.NAV_WINDOW_S
            if self._max_fraction >= self.COMPLETE_FRACTION:
                events.append(("completion", self._path))
            elif nav_recent and self._max_fraction <= self.SKIP_FRACTION:
                events.append(("skip", self._path))
        self._path = path
        self._max_fraction = max(0.0, fraction)
        self._suppress_departed = False
        return events


def record_watch_event(stats_file: str | Path, video_path: str, event: str) -> None:
    """Add one *event* ("completion" | "skip" | "lock") for *video_path*."""
    field = _EVENT_FIELDS[event]
    stats = load_watch_stats(stats_file)
    entry = stats.setdefault(
        normalize_path_key(video_path), {"completions": 0, "skips": 0, "locks": 0}
    )
    entry[field] = entry.get(field, 0) + 1
    # Videos moved away (marked weird, re-staged) must not leave orphan rows.
    stats = {key: value for key, value in stats.items() if Path(key).exists()}
    target = Path(stats_file)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    tmp.replace(target)
