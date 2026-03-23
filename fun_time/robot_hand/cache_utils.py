from __future__ import annotations

from collections import OrderedDict, deque
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def render_queue_for_frame_count(frame_count: int) -> deque[int]:
    return deque(range(max(0, frame_count)))


def trim_path_lru_cache(
    cache: OrderedDict[Path, T],
    *,
    limit: int,
    protected_paths: set[Path] | None = None,
) -> None:
    limit = max(1, int(limit))
    protected = {path for path in (protected_paths or set()) if path is not None}

    skipped = 0
    while len(cache) > limit and cache:
        oldest_key = next(iter(cache))
        if oldest_key in protected:
            cache.move_to_end(oldest_key)
            skipped += 1
            if skipped >= len(cache):
                break
            continue
        cache.popitem(last=False)
        skipped = 0
