"""The branch-verification launch: a real session out of a worktree.

The whole mechanism turns on which checkout a config is read against. Every
relative path in it resolves against the checkout that imported the package — so
the live config read from a worktree quietly names files *inside that worktree*:
an empty ``favs.csv``, a broker launcher one directory above
``.claude/worktrees``, a Chrome shortcut that is untracked and therefore
nowhere. These tests load the generated config anchored on the worktree, which
is precisely how the branch session will read it, and assert it still lands on
the machine's real files.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from fun_time import branch_session
from fun_time.config import ProjectConfig, load_config
from fun_time.single_instance import MUTEX_ORCHESTRATOR, mutex_name_for_config

REPO_DIR = Path(__file__).resolve().parent.parent


def _write_config(path: Path) -> Path:
    """A config shaped like the machine's, with the same values left relative.

    ``favs.csv``, ``state``, the broker launcher and the browser shortcut are
    relative in the real one; the rest are absolute. Both kinds are here so the
    rewrite is tested on values that need pinning and values that must survive
    untouched.
    """
    raw = {
        "paths": {
            "ahk_exe": "C:/Program Files/AutoHotkey/v2/AutoHotkey64.exe",
            "python_exe": ".venv/Scripts/pythonw.exe",
            "nau_library_dirs": ["C:/library/main"],
            "portrait_dirs": ["C:/library/portrait"],
            "landscape_dirs": ["C:/library/landscape"],
            "weird_dir": "C:/library/misc",
            "clips_dir": "C:/clips",
            "audio_dir": "C:/audio",
            "favs_file": "favs.csv",
            "state_dir": "state",
            "genau_python_exe": "C:/genau/.venv/Scripts/pythonw.exe",
            "genau_config_path": "C:/genau/genau_config.json",
            "broker_tray_launcher": "../broker/launch_broker_tray.vbs",
        },
        "layout": {
            "main_monitor": 1,
            "secondary_monitor": 2,
            "primary_top_ratio": 0.72,
            "landscape_width_ratio": 0.66,
        },
        "audio_companion": {"host": "127.0.0.1", "port": 50556},
        "random_favs_browser": {
            "enabled": True,
            "shortcut_path": "Example Chrome.lnk",
            "user_data_dir": "C:/Chrome/User Data",
            "profile_name": "Example",
            "open_count": 10,
            "lazy_load": True,
        },
        "voice_control": {"enabled": True, "model_path": "vosk-model-small-en-us-0.15"},
        "regen": {
            "generate_video_url": "https://example.com/video",
            "generate_image_url": "https://example.com/create",
            "media_root": "generated",
            "metadata_root": "C:/metadata",
        },
        "vr": {"library_dirs": ["vr"], "tcode_udp_port": 50557},
    }
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


def _make_checkout(root: Path) -> Path:
    """Enough of a checkout for ``build_branch_config`` to accept it."""
    (root / "fun_time" / "static").mkdir(parents=True)
    (root / "fun_time" / "orchestrator.py").write_text("", encoding="utf-8")
    return root


@pytest.fixture
def checkouts(tmp_path: Path) -> SimpleNamespace:
    primary = _make_checkout(tmp_path / "fun_time")
    worktree = _make_checkout(tmp_path / "fun_time" / ".claude" / "worktrees" / "branch")
    return SimpleNamespace(
        primary=primary,
        worktree=worktree,
        config_path=_write_config(primary / "fun_time_config.json"),
    )


def _live_and_branch(checkouts) -> tuple[ProjectConfig, ProjectConfig]:
    """The live config, and the branch config as the worktree will read it back.

    The two loads differ in exactly one thing — the checkout each is anchored
    on — which is exactly what differs between the live session and a branch
    one.
    """
    live = load_config(checkouts.config_path, project_dir=checkouts.primary)
    branch_config = branch_session.build_branch_config(
        checkouts.worktree,
        primary_config_path=checkouts.config_path,
        primary=checkouts.primary,
    )
    return live, load_config(branch_config, project_dir=checkouts.worktree)


def test_every_path_a_branch_session_reads_is_the_one_the_live_session_reads(checkouts):
    """The whole config compared field by field, not a sample of it.

    A path this rewrite forgets resolves against the worktree instead, so the
    two configs stop matching — which makes the comparison, rather than a list
    of keys anybody has to remember to extend, what keeps the set complete.
    """
    live, branch = _live_and_branch(checkouts)

    assert replace(branch.paths, state_dir=live.paths.state_dir) == live.paths
    assert branch.random_favs_browser == live.random_favs_browser
    assert branch.regen == live.regen
    assert branch.vr == live.vr
    assert branch.audio_companion == live.audio_companion
    assert branch.loopback_port == live.loopback_port


def test_the_branch_session_keeps_its_state_inside_the_worktree(checkouts):
    """The one thing it must not share. ``state/`` holds the command files,
    playlists, thumbnails, logs and resume point a session writes as it runs, so
    a half-finished branch pointed at the live one's would corrupt what the
    user's next real session reads back."""
    live, branch = _live_and_branch(checkouts)

    assert branch.paths.state_dir == checkouts.worktree / branch_session.STATE_DIRNAME
    assert branch.paths.state_dir != live.paths.state_dir


def test_a_branch_session_and_the_live_one_take_the_same_mutex(checkouts):
    """Which is what makes two sessions impossible rather than unlikely.

    They would share the AHK hotkey shell (``#SingleInstance Force``, so the
    second evicts the first), three fixed UDP ports, the loopback port, one
    microphone, one broker and one set of monitors. Carrying the live identity
    means the second to start is turned away by Fun Time's own "already
    running" message — in either order, including the taskbar icon being
    double-clicked while a branch session is up.
    """
    live, branch = _live_and_branch(checkouts)

    assert branch.instance_id == live.instance_id
    assert mutex_name_for_config(MUTEX_ORCHESTRATOR, branch.instance_id) == mutex_name_for_config(
        MUTEX_ORCHESTRATOR, live.instance_id
    )


def test_the_generated_config_lands_where_git_ignores_it(checkouts):
    """It is the live config with the machine's real library paths in it. The
    worktree is a checkout of a public repo, so the only safe place to put it is
    one git already ignores."""

    written = branch_session.build_branch_config(
        checkouts.worktree,
        primary_config_path=checkouts.config_path,
        primary=checkouts.primary,
    )

    assert written == checkouts.worktree / "state" / branch_session.BRANCH_CONFIG_NAME
    ignored = (REPO_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "state/" in ignored


def test_the_primary_checkout_is_refused(checkouts):
    """Pointing this at the primary would aim a "branch" session's state dir at
    the live session's own — and there is already a launcher for that checkout."""

    with pytest.raises(ValueError, match="launch.vbs"):
        branch_session.build_branch_config(
            checkouts.primary,
            primary_config_path=checkouts.config_path,
            primary=checkouts.primary,
        )


def test_a_directory_that_is_not_a_checkout_is_refused(checkouts, tmp_path):
    """Named as such, rather than left to fail later as a missing hotkey script
    somewhere inside startup."""
    empty = tmp_path / "not-a-checkout"
    empty.mkdir()

    with pytest.raises(FileNotFoundError, match="not a Fun Time checkout"):
        branch_session.build_branch_config(
            empty, primary_config_path=checkouts.config_path, primary=checkouts.primary
        )


def test_the_private_overlays_follow_the_session_into_the_worktree(checkouts):
    """``content.local.json`` is git-ignored and found relative to the package
    that reads it, so a worktree has none and falls back to the committed
    placeholder — the browser's filters come up ``alpha``/``beta``/``gamma`` and
    the user is looking at a wrongness the branch did not cause."""
    (checkouts.primary / "content.local.json").write_text(
        json.dumps({"studios": ["Example Studio"]}), encoding="utf-8"
    )
    userscript = checkouts.primary / "fun_time" / "static" / "regen_autofill.user.js"
    userscript.write_text("// autofill", encoding="utf-8")

    branch_session.build_branch_config(
        checkouts.worktree,
        primary_config_path=checkouts.config_path,
        primary=checkouts.primary,
    )

    assert json.loads((checkouts.worktree / "content.local.json").read_text(encoding="utf-8")) == {
        "studios": ["Example Studio"]
    }
    assert (
        checkouts.worktree / "fun_time" / "static" / "regen_autofill.user.js"
    ).read_text(encoding="utf-8") == "// autofill"


def test_an_overlay_edited_since_the_last_launch_is_refreshed(checkouts):
    """Copied on every launch, so a worktree kept around for days never runs on
    a vocabulary the user has since changed."""
    overlay = checkouts.primary / "content.local.json"
    overlay.write_text(json.dumps({"studios": ["First Studio"]}), encoding="utf-8")
    kwargs = dict(primary_config_path=checkouts.config_path, primary=checkouts.primary)
    branch_session.build_branch_config(checkouts.worktree, **kwargs)

    overlay.write_text(json.dumps({"studios": ["Second Studio"]}), encoding="utf-8")
    branch_session.build_branch_config(checkouts.worktree, **kwargs)

    assert json.loads((checkouts.worktree / "content.local.json").read_text(encoding="utf-8")) == {
        "studios": ["Second Studio"]
    }


def test_the_real_config_is_never_one_of_the_overlays_carried_over():
    """A copy of ``fun_time_config.json`` loose in a worktree is a live config
    nobody passes ``--config`` — and the branch config written under ``state/``
    is the entire point of this module."""
    assert not any("fun_time_config" in str(path) for path in branch_session._PRIVATE_OVERLAYS)


def test_the_picker_list_is_written_where_vbscript_can_read_it_back(tmp_path):
    """UTF-16 with a BOM is the one encoding FileSystemObject reads losslessly.
    Commit subjects are full of em dashes, and anything else hands the picker a
    line of mojibake."""
    listed = [
        branch_session.Worktree(
            path=Path("C:/checkouts/branch"),
            branch="claude/example",
            age="2 hours ago",
            subject="Widen the spine — again",
        )
    ]
    destination = tmp_path / branch_session.WORKTREE_LIST_NAME

    branch_session.write_worktree_list(destination, listed)

    assert destination.read_bytes().startswith(b"\xff\xfe")
    path, label = destination.read_text(encoding="utf-16").rstrip("\n").split("\t")
    assert Path(path) == Path("C:/checkouts/branch")
    assert label == "claude/example — Widen the spine — again (2 hours ago)"


def test_a_long_subject_gives_way_before_the_branch_name_does():
    """The menu's whole budget is a few hundred characters — InputBox truncates
    a prompt past about a thousand — and the branch name is the part an agent
    hands the user, so it is never what gets cut."""
    worktree = branch_session.Worktree(
        path=Path("C:/checkouts/branch"),
        branch="claude/a-fairly-long-branch-name",
        age="2 hours ago",
        subject="A commit subject long enough that it cannot possibly fit beside all that",
    )

    label = worktree.label

    assert len(label) <= branch_session.LABEL_WIDTH
    assert label.startswith("claude/a-fairly-long-branch-name — ")
    assert label.endswith("… (2 hours ago)")


def _git(repo: Path, *args: str, when: str | None = None) -> None:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Example Agent",
        "GIT_AUTHOR_EMAIL": "agent@example.com",
        "GIT_COMMITTER_NAME": "Example Agent",
        "GIT_COMMITTER_EMAIL": "agent@example.com",
    }
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", *args], cwd=str(repo), env=env, check=True, capture_output=True)


@pytest.fixture
def repo_with_worktrees(tmp_path: Path) -> SimpleNamespace:
    """A throwaway repo with two worktrees, committed at known times."""
    primary = tmp_path / "primary"
    primary.mkdir()
    _git(primary, "-c", "init.defaultBranch=main", "init")
    (primary / "readme.txt").write_text("example", encoding="utf-8")
    _git(primary, "add", "readme.txt")
    _git(primary, "commit", "-m", "First commit", when="2026-01-01T12:00:00")

    older = tmp_path / "older"
    newer = tmp_path / "newer"
    _git(primary, "worktree", "add", "-b", "example/older", str(older))
    _git(older, "commit", "--allow-empty", "-m", "Older work", when="2026-02-01T12:00:00")
    _git(primary, "worktree", "add", "-b", "example/newer", str(newer))
    _git(newer, "commit", "--allow-empty", "-m", "Newer work", when="2026-03-01T12:00:00")
    return SimpleNamespace(primary=primary, older=older, newer=newer)


def test_the_worktrees_are_listed_newest_first_without_the_primary(repo_with_worktrees):
    """Newest first because the branch an agent just finished is the one being
    verified; the primary is left out because it is what ``launch.vbs`` runs."""
    listed = branch_session.list_worktrees(repo_with_worktrees.primary)

    assert [worktree.branch for worktree in listed] == ["example/newer", "example/older"]
    assert [worktree.path for worktree in listed] == [
        repo_with_worktrees.newer.resolve(),
        repo_with_worktrees.older.resolve(),
    ]
    assert [worktree.subject for worktree in listed] == ["Newer work", "Older work"]
    assert all(worktree.age for worktree in listed)


def test_a_worktree_whose_directory_is_gone_is_not_offered(repo_with_worktrees):
    """Deleting a worktree without pruning leaves it registered. Offering it
    would put a menu entry there that can only fail."""
    shutil.rmtree(repo_with_worktrees.older)

    listed = branch_session.list_worktrees(repo_with_worktrees.primary)

    assert [worktree.branch for worktree in listed] == ["example/newer"]


def test_the_primary_is_found_from_a_worktree(repo_with_worktrees):
    """Worktrees share the primary's git directory, so the launcher finds the
    machine's real config and overlays from any of them."""
    assert branch_session.primary_checkout(repo_with_worktrees.newer) == (
        repo_with_worktrees.primary.resolve()
    )
