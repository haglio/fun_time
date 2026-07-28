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
import sys
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


def test_the_broker_keeps_its_own_corner_of_state_in_the_primary(checkouts):
    """The state dir moves into the worktree; the broker's files stay behind.

    ``../broker`` opens its heartbeat, serial-activity, command and mode files
    from the one directory its own config names, and never learns that a session
    moved.  Letting them follow ``state_dir`` pointed a branch session at a
    directory the broker has never written: the primary console's broker light
    red and its OSR2 light "off" while the device was plainly being driven, and
    park/resume written where nothing consumes them.
    """
    live, branch = _live_and_branch(checkouts)

    assert branch.paths.broker_state_dir == live.paths.state_dir
    assert branch.paths.broker_state_dir != branch.paths.state_dir
    assert [
        path.parent
        for path in (
            branch.broker_heartbeat_file,
            branch.osr2_serial_rx_file,
            branch.broker_cmd_file,
            branch.genau_mode_file,
            branch.genau_enabled_file,
        )
    ] == [live.paths.state_dir] * 5


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


@pytest.fixture
def primary_with_launcher(repo_with_worktrees) -> SimpleNamespace:
    """The throwaway repo, with the files a shortcut has to point at."""
    (repo_with_worktrees.primary / branch_session.LAUNCHER_NAME).write_text("' launcher", encoding="utf-8")
    (repo_with_worktrees.primary / "icon.ico").write_bytes(b"\x00")
    return repo_with_worktrees


pytestmark_shortcut = pytest.mark.skipif(
    sys.platform != "win32", reason="writes a real Windows shortcut"
)


@pytestmark_shortcut
def test_the_agent_leaves_a_shortcut_named_for_its_branch(primary_with_launcher):
    """This is the whole interface he sees: a file in the folder he keeps open,
    named after the branch the agent told him about.  Nothing to pick and no
    command line — the worktree is baked into the shortcut."""
    written = branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    assert written == primary_with_launcher.primary / "Verify example-newer.lnk"
    assert branch_session._generated_shortcuts(primary_with_launcher.primary) == {
        written: primary_with_launcher.newer.resolve()
    }


@pytestmark_shortcut
def test_a_shortcut_runs_the_launcher_that_is_current_when_it_is_clicked(primary_with_launcher):
    """It points at ``launch_branch.vbs`` in the primary rather than carrying
    the launch itself, so one made weeks ago picks up today's launcher instead
    of replaying an old one."""
    written = branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    target, arguments = branch_session._read_shortcuts(primary_with_launcher.primary)[written]

    assert Path(target).name.lower() == "wscript.exe"
    assert branch_session.LAUNCHER_NAME in arguments
    # The branch rides along so a failed launch can name it rather than a path.
    assert "example/newer" in arguments


@pytestmark_shortcut
def test_a_shortcut_for_a_deleted_worktree_is_cleared_away(primary_with_launcher):
    """Worktrees go when their branch lands, and this repo carries dozens of
    them — without a sweep his folder fills with files that can only fail."""
    stale = branch_session.write_launch_shortcut(
        primary_with_launcher.older, primary=primary_with_launcher.primary
    )
    shutil.rmtree(primary_with_launcher.older)

    branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    assert not stale.exists()
    assert (primary_with_launcher.primary / "Verify example-newer.lnk").is_file()


@pytestmark_shortcut
def test_the_sweep_only_ever_deletes_shortcuts_this_module_wrote(primary_with_launcher, tmp_path):
    """It runs in a folder full of his own files.  A name proves nothing, so
    what it deletes has to be provably ours — the arguments naming the branch
    launcher — and a shortcut of his that happens to start with the same word
    is left where it is."""
    decoy = primary_with_launcher.primary / "Verify something of his own.lnk"
    branch_session._write_shortcut(
        decoy,
        target=str(tmp_path / "nothing.exe"),
        arguments="",
        working_dir=str(tmp_path),
        icon="",
        description="his own",
    )

    branch_session.prune_stale_shortcuts(primary_with_launcher.primary)

    assert decoy.is_file()


def test_a_worktree_on_no_branch_is_named_after_its_directory():
    """``git worktree add`` without ``-b`` leaves a detached checkout, which has
    no branch name to put in a filename."""
    detached = Path("C:/checkouts/.claude/worktrees/wonderful-ellis-fbdb9e")

    assert branch_session.shortcut_name(detached, branch_session.DETACHED) == (
        "Verify wonderful-ellis-fbdb9e.lnk"
    )


def test_a_branch_name_becomes_a_filename_windows_will_take():
    """Branch names carry slashes; filenames may not."""
    assert branch_session.shortcut_name(Path("C:/wt"), "claude/some-branch") == (
        "Verify claude-some-branch.lnk"
    )


def test_a_shortcut_is_refused_before_the_launcher_has_landed(repo_with_worktrees):
    """A shortcut pointing at a launcher that is not in the primary yet does
    nothing at all when it is clicked, which is worse than not existing."""
    with pytest.raises(FileNotFoundError, match=branch_session.LAUNCHER_NAME):
        branch_session.write_launch_shortcut(
            repo_with_worktrees.newer, primary=repo_with_worktrees.primary
        )


def _live_state(checkouts) -> Path:
    """The live session's state dir, with something of each kind in it."""
    state = checkouts.primary / "state"
    (state / "hud_thumbnails").mkdir(parents=True)
    (state / "hud_thumbnails" / "abc123.jpg").write_bytes(b"thumbnail")
    (state / "nau_durations.json").write_text(json.dumps({"C:/library/main/one.mp4": {"ms": 1}}), encoding="utf-8")
    (state / "watch_stats.json").write_text(json.dumps({"C:/library/main/one.mp4": {"seconds": 90}}), encoding="utf-8")
    # Session state, which must stay the branch session's own.
    (state / "nau_playlist.tsv").write_text("C:/library/main/one.mp4\n", encoding="utf-8")
    (state / "dashboard_cmd.txt").write_text("portrait_lock", encoding="utf-8")
    (state / "shared_state.ini").write_text("[state]\n", encoding="utf-8")
    return state


def test_the_library_caches_are_started_from_the_live_sessions(checkouts):
    """Startup waits for Nau to report the video it is opening, and Nau reports
    nothing until it has a duration for it — so against a cold
    ``nau_durations.json`` it probed the whole library first and a branch launch
    took 45 seconds where the live session takes 4.  The thumbnail cache and the
    watch stats are the same cost paid later: blank HUD maps and an empty
    breeding view, neither of them the branch's doing."""
    _live_state(checkouts)

    branch_config = branch_session.build_branch_config(
        checkouts.worktree, primary_config_path=checkouts.config_path, primary=checkouts.primary
    )

    state = branch_config.parent
    assert json.loads((state / "nau_durations.json").read_text(encoding="utf-8")) == {
        "C:/library/main/one.mp4": {"ms": 1}
    }
    assert (state / "watch_stats.json").is_file()
    assert (state / "hud_thumbnails" / "abc123.jpg").read_bytes() == b"thumbnail"


def test_nothing_describing_the_session_itself_is_seeded(checkouts):
    """The separate state dir exists so a half-finished branch cannot corrupt
    what the live session reads back.  Seeding a playlist, a command file or the
    resume point would hand back exactly what it prevents."""
    _live_state(checkouts)

    branch_config = branch_session.build_branch_config(
        checkouts.worktree, primary_config_path=checkouts.config_path, primary=checkouts.primary
    )

    state = branch_config.parent
    assert not (state / "nau_playlist.tsv").exists()
    assert not (state / "dashboard_cmd.txt").exists()
    assert not (state / "shared_state.ini").exists()


def test_a_branch_sessions_own_cache_is_not_overwritten_by_an_older_one(checkouts):
    """A seed, not a sync.  A worktree kept around for days has its own newer
    durations by then, and rolling them back would make it index again."""
    live = _live_state(checkouts)
    branch_state = checkouts.worktree / "state"
    branch_state.mkdir(parents=True)
    (branch_state / "nau_durations.json").write_text(json.dumps({"newer": {}}), encoding="utf-8")
    stale = (live / "nau_durations.json").stat().st_mtime - 60
    os.utime(live / "nau_durations.json", (stale, stale))

    branch_session.seed_derived_caches(live, branch_state)

    assert json.loads((branch_state / "nau_durations.json").read_text(encoding="utf-8")) == {"newer": {}}


def test_the_thumbnail_cache_is_only_ever_topped_up(checkouts):
    """A thumbnail is named for its video and that video's modification time, so
    one already there can never be out of date — every launch after the first
    copies nothing rather than thousands of files."""
    live = _live_state(checkouts)
    branch_state = checkouts.worktree / "state"
    (branch_state / "hud_thumbnails").mkdir(parents=True)
    (branch_state / "hud_thumbnails" / "abc123.jpg").write_bytes(b"already here")
    (live / "hud_thumbnails" / "def456.jpg").write_bytes(b"new one")

    seeded = branch_session.seed_derived_caches(live, branch_state)

    assert (branch_state / "hud_thumbnails" / "abc123.jpg").read_bytes() == b"already here"
    assert (branch_state / "hud_thumbnails" / "def456.jpg").read_bytes() == b"new one"
    assert branch_state / "hud_thumbnails" / "abc123.jpg" not in seeded


@pytestmark_shortcut
def test_an_agent_takes_its_shortcut_back_out_once_the_work_lands(primary_with_launcher):
    """The branch is in Fun Time by then, so a file still offering to run it
    separately is clutter he has to reason about — and nothing else sweeps it
    until some other agent happens to write one."""
    written = branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    removed = branch_session.remove_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    assert removed == written
    assert not written.exists()


@pytestmark_shortcut
def test_removing_a_shortcut_leaves_every_other_branch_alone(primary_with_launcher):
    """Several branches are usually in flight at once.  Matching is on the
    worktree a shortcut runs, so taking one out never disturbs another agent's."""
    mine = branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )
    someone_elses = branch_session.write_launch_shortcut(
        primary_with_launcher.older, primary=primary_with_launcher.primary
    )

    branch_session.remove_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    assert not mine.exists()
    assert someone_elses.is_file()


@pytestmark_shortcut
def test_removing_a_shortcut_that_was_never_written_is_not_an_error(primary_with_launcher):
    """An agent told to always clean up should not have to remember whether it
    ever made one."""
    assert branch_session.remove_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    ) is None
