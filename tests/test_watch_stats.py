from __future__ import annotations

import random
from pathlib import Path

from fun_time.media_metadata import normalize_path_key
from fun_time.watch_stats import (
    load_watch_stats,
    passes_inclusion,
    record_watch_event,
    weight_for,
    weighted_shuffle,
)


def _touch(tmp_path: Path, name: str) -> str:
    p = tmp_path / name
    p.write_text("x", encoding="utf-8")
    return str(p)


def test_record_watch_event_accumulates_counts_per_video(tmp_path: Path):
    stats_file = tmp_path / "state" / "watch_stats.json"
    video = _touch(tmp_path, "clip.mp4")

    record_watch_event(stats_file, video, "completion")
    record_watch_event(stats_file, video, "completion")
    record_watch_event(stats_file, video, "skip")
    record_watch_event(stats_file, video, "lock")

    stats = load_watch_stats(stats_file)
    entry = stats[normalize_path_key(video)]
    assert entry == {"completions": 2, "skips": 1, "locks": 1}


def test_load_watch_stats_returns_empty_when_missing_or_corrupt(tmp_path: Path):
    assert load_watch_stats(tmp_path / "absent.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert load_watch_stats(bad) == {}


# --- weight_for ---


def test_weight_for_scales_up_with_completions_and_down_with_skips():
    neutral = weight_for(None)
    watched = weight_for({"completions": 3, "skips": 0, "locks": 0})
    skipped = weight_for({"completions": 0, "skips": 3, "locks": 0})

    assert neutral == 1.0
    assert watched == 2.0
    assert skipped == 0.5
    # Locks are the strongest positive signal: one lock outweighs one completion.
    assert weight_for({"completions": 0, "skips": 0, "locks": 1}) > weight_for(
        {"completions": 1, "skips": 0, "locks": 0}
    )


def test_weight_for_is_clamped_to_an_eighth_and_eightfold():
    assert weight_for({"completions": 100, "skips": 0, "locks": 50}) == 8.0
    assert weight_for({"completions": 0, "skips": 100, "locks": 0}) == 0.125


def test_record_watch_event_prunes_entries_for_vanished_files(tmp_path: Path):
    """A video moved away (e.g. marked weird) should not leave a stats orphan."""
    stats_file = tmp_path / "watch_stats.json"
    keeper = _touch(tmp_path, "keeper.mp4")
    goner = _touch(tmp_path, "goner.mp4")
    record_watch_event(stats_file, keeper, "completion")
    record_watch_event(stats_file, goner, "skip")

    Path(goner).unlink()
    record_watch_event(stats_file, keeper, "completion")

    stats = load_watch_stats(stats_file)
    assert normalize_path_key(goner) not in stats
    assert stats[normalize_path_key(keeper)]["completions"] == 2


# --- weighted ordering primitives ---


def test_weighted_shuffle_front_loads_heavy_items():
    rng = random.Random(7)
    weights = {"heavy.mp4": 8.0, "light.mp4": 0.125}

    heavy_first = sum(
        weighted_shuffle(["light.mp4", "heavy.mp4"], weights.get, rng)[0] == "heavy.mp4"
        for _ in range(200)
    )

    assert heavy_first > 150


def test_weighted_shuffle_preserves_items_and_handles_empty():
    rng = random.Random(1)
    paths = [f"clip{i}.mp4" for i in range(10)]

    result = weighted_shuffle(paths, lambda _p: 1.0, rng)

    assert sorted(result) == sorted(paths)
    assert weighted_shuffle([], lambda _p: 1.0, rng) == []


def test_passes_inclusion_only_drops_below_neutral_weight():
    rng = random.Random(3)
    assert all(passes_inclusion(1.0, rng) for _ in range(100))
    assert all(passes_inclusion(8.0, rng) for _ in range(100))
    kept = sum(passes_inclusion(0.5, rng) for _ in range(200))
    assert 60 < kept < 140
