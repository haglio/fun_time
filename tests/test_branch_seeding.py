from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fun_time import branch_seeding
from fun_time.config import DEFAULT_CONFIG_PATH

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def live(tmp_path: Path) -> Path:
    state = tmp_path / "live_state"
    (state / "hud_thumbnails").mkdir(parents=True)
    (state / "hud_thumbnails" / "abc123.jpg").write_bytes(b"thumbnail")
    (state / "watch_stats.json").write_text(
        json.dumps({"C:/library/main/one.mp4": {"seconds": 90}}), encoding="utf-8"
    )
    return state


@pytest.fixture
def branch_state(tmp_path: Path) -> Path:
    state = tmp_path / "branch_state"
    state.mkdir()
    return state


def test_a_branch_sessions_own_watch_stats_are_not_rolled_back_by_older_ones(live, branch_state):
    (branch_state / "watch_stats.json").write_text(json.dumps({"newer": {}}), encoding="utf-8")
    stale = (live / "watch_stats.json").stat().st_mtime - 60
    os.utime(live / "watch_stats.json", (stale, stale))

    branch_seeding.seed_what_is_costly_to_rebuild(live, branch_state)

    assert json.loads((branch_state / "watch_stats.json").read_text(encoding="utf-8")) == {"newer": {}}


def test_nothing_describing_the_session_itself_is_seeded(live, branch_state):
    for name in ("main_player_playlist.tsv", "dashboard_cmd.txt", "shared_state.ini"):
        (live / name).write_text("live session's own", encoding="utf-8")

    branch_seeding.seed_what_is_costly_to_rebuild(live, branch_state)

    for name in ("main_player_playlist.tsv", "dashboard_cmd.txt", "shared_state.ini"):
        assert not (branch_state / name).exists()


def test_the_overlays_git_keeps_out_of_a_worktree_are_copied_in(tmp_path):
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert all(f"/{overlay.as_posix()}" in ignored for overlay in branch_seeding.PRIVATE_OVERLAYS)
    primary, worktree = tmp_path / "primary", tmp_path / "worktree"
    (primary / "fun_time" / "static").mkdir(parents=True)
    (primary / "content.local.json").write_text(
        json.dumps({"studios": ["Example Studio"]}), encoding="utf-8"
    )
    (primary / "fun_time" / "static" / "regen_autofill.user.js").write_text(
        "// autofill", encoding="utf-8"
    )
    worktree.mkdir()

    copied = branch_seeding.mirror_private_overlays(primary, worktree)

    assert json.loads((worktree / "content.local.json").read_text(encoding="utf-8")) == {
        "studios": ["Example Studio"]
    }
    assert (
        worktree / "fun_time" / "static" / "regen_autofill.user.js"
    ).read_text(encoding="utf-8") == "// autofill"
    assert len(copied) == 2


def test_an_overlay_the_primary_does_not_have_is_simply_not_copied(tmp_path):
    primary, worktree = tmp_path / "primary", tmp_path / "worktree"
    primary.mkdir()
    worktree.mkdir()

    assert branch_seeding.mirror_private_overlays(primary, worktree) == []


def test_no_real_config_lands_where_a_session_given_no_config_would_read_it(tmp_path):
    primary, worktree = tmp_path / "primary", tmp_path / "worktree"
    primary.mkdir()
    worktree.mkdir()
    (primary / DEFAULT_CONFIG_PATH.name).write_text("{}", encoding="utf-8")

    branch_seeding.mirror_private_overlays(primary, worktree)

    assert not (worktree / DEFAULT_CONFIG_PATH.name).exists()


def test_a_branch_writing_its_seeded_cache_leaves_the_live_sessions_alone(live, branch_state):
    as_the_live_session_left_it = (live / "watch_stats.json").read_text(encoding="utf-8")
    branch_seeding.seed_what_is_costly_to_rebuild(live, branch_state)

    with (branch_state / "watch_stats.json").open("r+", encoding="utf-8") as seeded:
        seeded.write(json.dumps({"unfinished": {}}))

    assert (live / "watch_stats.json").read_text(encoding="utf-8") == as_the_live_session_left_it


