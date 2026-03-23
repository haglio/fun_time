from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from fun_time.robot_hand.cache_utils import render_queue_for_frame_count, trim_path_lru_cache


class TestRenderQueueForFrameCount:
    def test_returns_empty_queue_for_zero_frames(self):
        assert list(render_queue_for_frame_count(0)) == []

    def test_returns_sequential_indices(self):
        assert list(render_queue_for_frame_count(4)) == [0, 1, 2, 3]


class TestTrimPathLruCache:
    def test_trims_oldest_unprotected_entries(self, tmp_path: Path):
        cache: OrderedDict[Path, str] = OrderedDict(
            [
                (tmp_path / "a", "a"),
                (tmp_path / "b", "b"),
                (tmp_path / "c", "c"),
            ]
        )

        trim_path_lru_cache(cache, limit=2)

        assert list(cache.values()) == ["b", "c"]

    def test_keeps_protected_entry_when_trimming(self, tmp_path: Path):
        protected = tmp_path / "a"
        cache: OrderedDict[Path, str] = OrderedDict(
            [
                (protected, "a"),
                (tmp_path / "b", "b"),
                (tmp_path / "c", "c"),
            ]
        )

        trim_path_lru_cache(cache, limit=2, protected_paths={protected})

        assert set(cache.values()) == {"a", "c"}

    def test_stops_when_all_entries_are_protected(self, tmp_path: Path):
        a = tmp_path / "a"
        b = tmp_path / "b"
        cache: OrderedDict[Path, str] = OrderedDict([(a, "a"), (b, "b")])

        trim_path_lru_cache(cache, limit=1, protected_paths={a, b})

        assert set(cache.values()) == {"a", "b"}
