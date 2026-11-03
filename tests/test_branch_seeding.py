from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from fun_time import branch_seeding


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

    branch_seeding.seed_derived_caches(live, branch_state)

    assert json.loads((branch_state / "watch_stats.json").read_text(encoding="utf-8")) == {"newer": {}}


def test_the_thumbnail_cache_is_only_ever_topped_up(live, branch_state):
    (branch_state / "hud_thumbnails").mkdir(parents=True)
    (branch_state / "hud_thumbnails" / "abc123.jpg").write_bytes(b"already here")
    (live / "hud_thumbnails" / "def456.jpg").write_bytes(b"new one")

    seeded = branch_seeding.top_up_thumbnails(live, branch_state)

    assert (branch_state / "hud_thumbnails" / "abc123.jpg").read_bytes() == b"already here"
    assert (branch_state / "hud_thumbnails" / "def456.jpg").read_bytes() == b"new one"
    assert branch_state / "hud_thumbnails" / "abc123.jpg" not in seeded


def test_a_thumbnail_the_session_made_while_the_copy_ran_is_kept(live, branch_state, monkeypatch):
    real_copy = shutil.copyfile
    made_by_the_session = branch_state / "hud_thumbnails" / "abc123.jpg"

    def copy_while_the_session_makes_its_own(source, destination):
        made_by_the_session.parent.mkdir(parents=True, exist_ok=True)
        made_by_the_session.write_bytes(b"the session's own")
        return real_copy(source, destination)

    monkeypatch.setattr(branch_seeding.shutil, "copyfile", copy_while_the_session_makes_its_own)

    branch_seeding.top_up_thumbnails(live, branch_state)

    assert made_by_the_session.read_bytes() == b"the session's own"
    assert list((branch_state / "hud_thumbnails").iterdir()) == [made_by_the_session]


def test_a_copied_thumbnail_appears_whole_or_not_at_all(live, branch_state, monkeypatch):
    real_copy = shutil.copyfile
    arriving = branch_state / "hud_thumbnails" / "abc123.jpg"
    there_while_half_copied: list[bool] = []

    def copy_in_two_halves(source, destination):
        Path(destination).write_bytes(Path(source).read_bytes()[:4])
        there_while_half_copied.append(arriving.exists())
        return real_copy(source, destination)

    monkeypatch.setattr(branch_seeding.shutil, "copyfile", copy_in_two_halves)

    branch_seeding.top_up_thumbnails(live, branch_state)

    assert there_while_half_copied == [False]
    assert arriving.read_bytes() == b"thumbnail"


def test_nothing_describing_the_session_itself_is_seeded(live, branch_state):
    for name in ("main_player_playlist.tsv", "dashboard_cmd.txt", "shared_state.ini"):
        (live / name).write_text("live session's own", encoding="utf-8")

    branch_seeding.seed_derived_caches(live, branch_state)

    for name in ("main_player_playlist.tsv", "dashboard_cmd.txt", "shared_state.ini"):
        assert not (branch_state / name).exists()


def test_the_private_overlays_are_copied_into_the_worktree(tmp_path):
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


def test_the_real_config_is_never_one_of_the_overlays_carried_over():
    assert not any("fun_time_config" in str(path) for path in branch_seeding.PRIVATE_OVERLAYS)
