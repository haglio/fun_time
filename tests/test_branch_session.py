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
import re
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from app_support.win32 import mutex_name
from shared_ui.preview import Preview

from fun_time import branch_session, preview_marker
from fun_time.config import ProjectConfig, load_config
from fun_time.shortcuts import Shortcut, read_shortcuts, write_shortcut
from fun_time.single_instance import MUTEX_ORCHESTRATOR
from fun_time.win32_taskbar import APP_USER_MODEL_ID
from tests.git_repo import git


def _shortcut_at(primary, path) -> Shortcut:
    """The one shortcut *path* names, read back off disk."""
    pattern = f"{branch_session.SHORTCUT_PREFIX}*{branch_session.SHORTCUT_SUFFIX}"
    return read_shortcuts(primary, pattern=pattern)[path]

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
            "main_player_library_dirs": ["C:/library/main"],
            "portrait_dirs": ["C:/library/portrait"],
            "landscape_dirs": ["C:/library/landscape"],
            "weird_dir": "C:/library/misc",
            "clips_dir": "C:/clips",
            "audio_dir": "C:/audio",
            "favs_file": "favs.csv",
            "state_dir": "state",
            "genau_python_exe": "C:/genau/.venv/Scripts/pythonw.exe",
            "genau_config_path": "C:/genau/genau_config.json",
            # Relative on purpose: a checkout Genau and the main player are run out of sits
            # one directory up from the primary, which from a worktree is a
            # different place entirely.
            "genau_project_dirs": ["../genau", "../player_core"],
            "broker_tray_launcher": "../broker/launch_broker_tray.vbs",
        },
        "layout": {
            "primary_monitor": 1,
            "secondary_monitor": 2,
            "main_top_ratio": 0.72,
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


def test_the_shortcut_line_names_the_sibling_checkouts_the_launch_will_run(checkouts):
    """An empty chain silently runs the venv installs — the session comes up
    fine and demonstrates nothing — so making a shortcut prints what the chain
    currently carries, for the agent to read before handing it over."""
    line = branch_session.sibling_checkouts_line(
        checkouts.worktree, checkouts.primary, config_path=checkouts.config_path)

    assert "genau" in line and "player_core" in line


def test_the_shortcut_line_says_when_the_chain_is_empty(checkouts):
    raw = json.loads(checkouts.config_path.read_text(encoding="utf-8"))
    raw["paths"]["genau_project_dirs"] = []
    checkouts.config_path.write_text(json.dumps(raw), encoding="utf-8")

    line = branch_session.sibling_checkouts_line(
        checkouts.worktree, checkouts.primary, config_path=checkouts.config_path)

    assert "primaries" in line


def test_the_shortcut_line_reads_the_worktrees_own_override_first(checkouts):
    """What the line says must be what the click will do, and the click applies
    the worktree's ``state/genau_project_dirs.txt`` over the machine's config —
    so the line resolves through the same override, or it would vouch for a
    chain the launch does not run."""
    state = checkouts.worktree / branch_session.STATE_DIRNAME
    state.mkdir(parents=True, exist_ok=True)
    (state / branch_session.GENAU_DIRS_OVERRIDE_NAME).write_text(
        "C:/checkouts/genau-fix\n", encoding="utf-8")

    line = branch_session.sibling_checkouts_line(
        checkouts.worktree, checkouts.primary, config_path=checkouts.config_path)

    assert "genau-fix" in line
    assert "player_core" not in line  # the override replaces, never augments


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
    """The state dir moves into the worktree; the broker's files stay where they are.

    ``../broker`` opens its heartbeat, serial-activity, command and mode files
    from the one directory its own config names, and never learns that a session
    moved.  Letting them follow ``state_dir`` pointed a branch session at a
    directory the broker has never written: the main console's broker light
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
            branch.broker_mode_file,
        )
    ] == [live.paths.state_dir] * 4


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
    assert mutex_name(MUTEX_ORCHESTRATOR, branch.instance_id) == mutex_name(
        MUTEX_ORCHESTRATOR, live.instance_id
    )


def _write_genau_override(checkouts, text: str) -> None:
    state = checkouts.worktree / branch_session.STATE_DIRNAME
    state.mkdir(parents=True, exist_ok=True)
    (state / branch_session.GENAU_DIRS_OVERRIDE_NAME).write_text(text, encoding="utf-8")


def test_a_worktree_can_name_the_genau_checkout_its_own_session_runs(checkouts):
    """Which checkout of ../genau the main player and Genau come from is a per-session fact,
    and until this it could only be said in the machine's one config — where it
    reached the user's ordinary session and every other agent's branch session
    too."""
    _write_genau_override(checkouts, "C:/genau/.claude/worktrees/mine\n")

    _live, branch = _live_and_branch(checkouts)

    assert branch.paths.genau_project_dirs == (Path("C:/genau/.claude/worktrees/mine"),)


def test_an_empty_override_means_the_plain_install_whatever_the_machine_says(checkouts):
    """The case that cost a round trip: a branch with nothing to do with genau,
    launched while the machine was pinned to somebody's unlanded genau worktree,
    and therefore running a genau that predated what had already landed."""
    _write_genau_override(checkouts, "# nothing of ours\n\n")

    live, branch = _live_and_branch(checkouts)

    assert live.paths.genau_project_dirs != ()
    assert branch.paths.genau_project_dirs == ()


def test_without_an_override_the_machines_own_answer_rides_through(checkouts):
    live, branch = _live_and_branch(checkouts)

    assert branch.paths.genau_project_dirs == live.paths.genau_project_dirs


def test_the_override_lands_where_git_ignores_it(checkouts):
    """It names paths on the machine, and the worktree is a checkout of a public
    repo — the same reason the branch config itself lives under ``state/``."""
    assert branch_session.GENAU_DIRS_OVERRIDE_NAME
    ignored = (REPO_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "state/" in ignored


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


@pytest.fixture
def repo_with_worktrees(tmp_path: Path) -> SimpleNamespace:
    """A throwaway repo with two worktrees, committed at known times."""
    primary = tmp_path / "primary"
    primary.mkdir()
    git(primary, "-c", "init.defaultBranch=main", "init")
    (primary / "readme.txt").write_text("example", encoding="utf-8")
    git(primary, "add", "readme.txt")
    git(primary, "commit", "-m", "First commit", when="2026-01-01T12:00:00")

    older = tmp_path / "older"
    newer = tmp_path / "newer"
    git(primary, "worktree", "add", "-b", "example/older", str(older))
    git(older, "commit", "--allow-empty", "-m", "Older work", when="2026-02-01T12:00:00")
    git(primary, "worktree", "add", "-b", "example/newer", str(newer))
    git(newer, "commit", "--allow-empty", "-m", "Newer work", when="2026-03-01T12:00:00")
    return SimpleNamespace(primary=primary, older=older, newer=newer)


def test_the_primary_is_found_from_a_worktree(repo_with_worktrees):
    """Worktrees share the main player's git directory, so the launcher finds the
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
def test_the_shortcut_lands_in_the_branchs_own_folder(primary_with_launcher):
    """He reaches it through the one-click link an agent hands him, so where the
    file sits is nobody's business but that branch's — and putting it there is
    what keeps it out of everyone else's way.  In his Fun Time folder it showed
    up in every other agent's tidiness check, and each of them told him about
    launchers that were not theirs to speak for."""
    written = branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    assert written == primary_with_launcher.newer / "Verify example-newer.lnk"
    assert branch_session._generated_shortcuts(primary_with_launcher.newer) == {
        written: primary_with_launcher.newer.resolve()
    }
    assert branch_session._generated_shortcuts(primary_with_launcher.primary) == {}, (
        "his Fun Time folder gains nothing"
    )


@pytestmark_shortcut
def test_a_shortcut_runs_the_launcher_that_is_current_when_it_is_clicked(primary_with_launcher):
    """It points at ``launch_branch.vbs`` in the main player rather than carrying
    the launch itself, so one made weeks ago picks up today's launcher instead
    of replaying an old one."""
    written = branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    found = _shortcut_at(primary_with_launcher.newer, written)

    assert Path(found.target).name.lower() == "wscript.exe"
    assert branch_session.LAUNCHER_NAME in found.arguments
    # The branch rides along so a failed launch can name it rather than a path.
    assert "example/newer" in found.arguments


@pytestmark_shortcut
def test_a_vr_shortcut_names_itself_and_asks_the_launcher_for_the_headset(primary_with_launcher):
    """The one file that opens a VR branch: same launcher, same worktree, one
    more argument — and the V so it reads as FunTimeVR rather than as another
    Fun Time."""
    written = branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary, vr=True
    )

    assert written == primary_with_launcher.newer / "Verify example-newer in VR.lnk"
    found = _shortcut_at(primary_with_launcher.newer, written)
    assert Path(found.target).name.lower() == "wscript.exe"
    assert branch_session.LAUNCHER_NAME in found.arguments
    assert branch_session.VR_LAUNCH_FLAG in found.arguments
    # Still discoverable as this module's, and still mapped to its worktree, so
    # the removal reaches it exactly as it reaches the desktop one.
    assert branch_session._generated_shortcuts(primary_with_launcher.newer) == {
        written: primary_with_launcher.newer.resolve()
    }


@pytestmark_shortcut
def test_both_flavours_of_one_branch_come_back_out_together(primary_with_launcher):
    """A branch can have had both left for him; landing it should clear both."""
    desktop = branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )
    headset = branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary, vr=True
    )

    removed = branch_session.remove_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    assert sorted(removed) == sorted([desktop, headset])
    assert not desktop.exists() and not headset.exists()


@pytestmark_shortcut
def test_one_the_old_placement_left_in_his_folder_is_swept_when_its_branch_goes(
    primary_with_launcher,
):
    """Every shortcut used to be written into his Fun Time folder, and the ones
    already sitting there outlive the change: a branch folder going is the only
    thing that can say they are finished with."""
    written = branch_session.write_launch_shortcut(
        primary_with_launcher.older, primary=primary_with_launcher.primary
    )
    legacy = primary_with_launcher.primary / written.name
    shutil.move(written, legacy)
    shutil.rmtree(primary_with_launcher.older)

    branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    assert not legacy.exists()
    assert (primary_with_launcher.newer / "Verify example-newer.lnk").is_file()


@pytestmark_shortcut
def test_the_sweep_only_ever_deletes_shortcuts_this_module_wrote(primary_with_launcher, tmp_path):
    """It runs in a folder full of his own files.  A name proves nothing, so
    what it deletes has to be provably ours — the arguments naming the branch
    launcher — and a shortcut of his that happens to start with the same word
    is left where it is."""
    decoy = primary_with_launcher.primary / "Verify something of his own.lnk"
    write_shortcut(
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


def test_a_vr_branch_says_so_in_its_own_filename():
    """A VR branch and a desktop one can be in flight at once, and they are two
    different sessions — so the file he double-clicks has to say which."""
    assert branch_session.shortcut_name(Path("C:/wt"), "claude/some-branch", vr=True) == (
        "Verify claude-some-branch in VR.lnk"
    )


def test_a_shortcut_is_refused_before_the_launcher_has_landed(repo_with_worktrees):
    """A shortcut pointing at a launcher that is not in the primary yet does
    nothing at all when it is clicked, which is worse than not existing."""
    with pytest.raises(FileNotFoundError, match=branch_session.LAUNCHER_NAME):
        branch_session.write_launch_shortcut(
            repo_with_worktrees.newer, primary=repo_with_worktrees.primary
        )


def test_a_shortcut_is_refused_for_a_worktree_missing_work_the_primary_has(primary_with_launcher):
    git(primary_with_launcher.primary, "commit", "--allow-empty", "-m",
         "Work that landed after the branch was cut", when="2026-04-01T12:00:00")

    with pytest.raises(branch_session.OutOfDateWorktree, match=r"missing 1 commit\b"):
        branch_session.write_launch_shortcut(
            primary_with_launcher.newer, primary=primary_with_launcher.primary
        )

    assert not (primary_with_launcher.primary / "Verify example-newer.lnk").exists()


def test_the_launch_seeds_the_branchs_state_from_the_live_sessions(checkouts):
    """Which files come across and on what rule is ``branch_seeding``'s; that a
    launch runs it at all is this module's, and the wiring is what breaks when
    the two drift."""
    state = checkouts.primary / "state"
    state.mkdir(parents=True)
    (state / "main_player_durations.json").write_text(
        json.dumps({"C:/library/main/one.mp4": {"ms": 1}}), encoding="utf-8"
    )

    branch_config = branch_session.build_branch_config(
        checkouts.worktree, primary_config_path=checkouts.config_path, primary=checkouts.primary
    )

    branch_state = branch_config.parent
    assert json.loads(
        (branch_state / "main_player_durations.json").read_text(encoding="utf-8")
    ) == {"C:/library/main/one.mp4": {"ms": 1}}


def _launch_with_one_live_thumbnail(checkouts, monkeypatch, start_the_session) -> None:
    live_thumbnails = checkouts.primary / "state" / "hud_thumbnails"
    live_thumbnails.mkdir(parents=True)
    (live_thumbnails / "abc123.jpg").write_bytes(b"thumbnail")
    monkeypatch.setattr(branch_session.subprocess, "Popen", start_the_session)
    monkeypatch.setattr(branch_session, "commits_missing", lambda worktree, primary: 0)
    branch_session.launch(checkouts.worktree, primary=checkouts.primary,
                          primary_config_path=checkouts.config_path)


def test_a_launch_starts_the_session_before_copying_the_thumbnails(checkouts, monkeypatch):
    branch_thumbnails = checkouts.worktree / "state" / "hud_thumbnails"
    thumbnails_as_the_session_started: list[list[str]] = []

    def start_the_session(command, **kwargs):
        thumbnails_as_the_session_started.append(
            sorted(path.name for path in branch_thumbnails.glob("*")))
        return SimpleNamespace(wait=lambda: 0)

    _launch_with_one_live_thumbnail(checkouts, monkeypatch, start_the_session)

    assert thumbnails_as_the_session_started == [[]]
    assert (branch_thumbnails / "abc123.jpg").read_bytes() == b"thumbnail"


def test_a_launch_logs_how_many_thumbnails_it_copied_and_how_long_that_took(
        checkouts, monkeypatch, capsys):
    _launch_with_one_live_thumbnail(
        checkouts, monkeypatch, lambda command, **kwargs: SimpleNamespace(wait=lambda: 0))

    assert re.search(r"^Copied 1 thumbnail from \S.* in \d+\.\ds$",
                     capsys.readouterr().out, re.MULTILINE)


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

    assert removed == [written]
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
def test_one_the_old_placement_left_in_his_folder_still_comes_out_on_landing(
    primary_with_launcher,
):
    """An agent that made its shortcut before the move still has to take that
    one out, or landing leaves exactly the file this change was to be rid of."""
    written = branch_session.write_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )
    legacy = primary_with_launcher.primary / written.name
    shutil.move(written, legacy)

    removed = branch_session.remove_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    )

    assert removed == [legacy]
    assert not legacy.exists()


@pytestmark_shortcut
def test_removing_a_shortcut_that_was_never_written_is_not_an_error(primary_with_launcher):
    """An agent told to always clean up should not have to remember whether it
    ever made one."""
    assert branch_session.remove_launch_shortcut(
        primary_with_launcher.newer, primary=primary_with_launcher.primary
    ) == []


def _write_origenerator_override(checkouts, text: str) -> None:
    state = checkouts.worktree / branch_session.STATE_DIRNAME
    state.mkdir(parents=True, exist_ok=True)
    (state / branch_session.ORIGENERATOR_DIR_OVERRIDE_NAME).write_text(text, encoding="utf-8")


def test_a_worktree_can_name_the_origenerator_checkout_its_session_hosts(checkouts):
    """Same per-session fact as the genau override, same shape: an agent judging
    an origenerator branch points its own session at that worktree, never the
    machine's one config."""
    _write_origenerator_override(checkouts, "C:/origenerator/.claude/worktrees/mine\n")

    _live, branch = _live_and_branch(checkouts)

    assert branch.paths.origenerator_dir == Path("C:/origenerator/.claude/worktrees/mine")


def test_an_empty_origenerator_override_hosts_none_at_all(checkouts):
    _write_origenerator_override(checkouts, "# none\n")

    _live, branch = _live_and_branch(checkouts)

    assert branch.paths.origenerator_dir is None


class _RecordedStart:
    """Stands in for subprocess.Popen, keeping what launch() asked for."""

    def __init__(self):
        self.command: list[str] | None = None
        self.cwd: str | None = None
        self.kwargs: dict = {}

    def __call__(self, command, cwd=None, **kwargs):
        self.command, self.cwd, self.kwargs = list(command), cwd, kwargs
        return SimpleNamespace(wait=lambda: 0)


def _launch_recorded(monkeypatch, tmp_path: Path, **kwargs) -> _RecordedStart:
    recorded = _RecordedStart()
    monkeypatch.setattr(
        branch_session, "build_branch_config", lambda worktree, **_: tmp_path / "cfg.json"
    )
    monkeypatch.setattr(branch_session, "top_up_branch_thumbnails", lambda worktree, **_: None)
    monkeypatch.setattr(branch_session, "primary_checkout", lambda start=None: tmp_path)
    monkeypatch.setattr(branch_session, "commits_missing", lambda worktree, primary: 0)
    monkeypatch.setattr(branch_session.subprocess, "Popen", recorded)
    branch_session.launch(tmp_path, **kwargs)
    return recorded


def test_a_branch_session_runs_the_desktop_orchestrator(monkeypatch, tmp_path: Path):
    recorded = _launch_recorded(monkeypatch, tmp_path)

    assert "fun_time.orchestrator" in recorded.command
    assert recorded.cwd == str(tmp_path.resolve())
    assert recorded.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW


def test_a_branch_session_writes_its_output_into_the_launchers_log(monkeypatch, tmp_path: Path):
    recorded = _launch_recorded(monkeypatch, tmp_path)

    assert recorded.kwargs["stdout"] is sys.stdout
    assert recorded.kwargs["stderr"] is sys.stderr


def test_every_process_of_a_branch_session_is_told_it_is_one(monkeypatch, tmp_path: Path):
    recorded = _launch_recorded(monkeypatch, tmp_path)

    assert recorded.kwargs["env"][preview_marker.FLAG] == "1"
    assert recorded.kwargs["env"]["PATH"] == os.environ["PATH"]


def test_a_vr_branch_session_runs_the_vr_orchestrator(monkeypatch, tmp_path: Path):
    """The whole of what --vr changes: same config, same working directory that
    swaps the code, a different entry point on top of it."""
    recorded = _launch_recorded(monkeypatch, tmp_path, vr=True)

    assert "fun_time_vr.orchestrator" in recorded.command
    assert "fun_time.orchestrator" not in recorded.command
    assert recorded.cwd == str(tmp_path.resolve())


def _sessions_exit_with(monkeypatch, returncode: int) -> list[list[str]]:
    """Every session the launcher goes on to start, as the command it ran."""
    real_popen = subprocess.Popen
    sessions: list[list[str]] = []

    def popen(command, *args, **kwargs):
        if command[0] == "git":
            return real_popen(command, *args, **kwargs)
        sessions.append(list(command))
        return SimpleNamespace(wait=lambda: returncode)

    monkeypatch.setattr(branch_session, "build_branch_config",
                        lambda worktree, **_: worktree / "state" / "cfg.json")
    monkeypatch.setattr(branch_session, "top_up_branch_thumbnails", lambda worktree, **_: None)
    monkeypatch.setattr(branch_session.subprocess, "Popen", popen)
    return sessions


def _land_work_on_the_primary(checkouts) -> None:
    git(checkouts.primary, "commit", "--allow-empty", "-m",
         "Work that landed after the branch was cut", when="2026-04-01T12:00:00")


def _note_left_in(worktree: Path) -> Path:
    return worktree / "state" / branch_session.OUT_OF_DATE_NOTE_NAME


def test_a_worktree_missing_work_the_primary_has_is_refused_before_a_room_opens(
        repo_with_worktrees, monkeypatch):
    """The sibling checkouts a session runs against move with the primary, so a
    branch left on an older Fun Time can start things this computer no longer has; refused, it
    says so in his words instead of opening a room that dies."""
    _land_work_on_the_primary(repo_with_worktrees)
    sessions = _sessions_exit_with(monkeypatch, 0)

    returncode = branch_session.launch(
        repo_with_worktrees.newer, primary=repo_with_worktrees.primary)

    assert returncode == 1
    assert sessions == []
    note = _note_left_in(repo_with_worktrees.newer).read_text(encoding="utf-8")
    assert "Fun Time couldn't start on example/newer" in note
    assert "1 change older than the one you normally run" in note
    assert "bring its branch up to date and make you a new launcher" in note


def test_the_refusal_tells_the_agent_to_rebase(repo_with_worktrees, monkeypatch, capsys):
    _land_work_on_the_primary(repo_with_worktrees)
    _sessions_exit_with(monkeypatch, 0)

    branch_session.launch(repo_with_worktrees.newer, primary=repo_with_worktrees.primary)

    printed = capsys.readouterr().out
    assert "Refused: " in printed
    assert "missing 1 commit the primary checkout has" in printed
    assert "origin/main" in printed


def test_a_worktree_carrying_everything_the_primary_has_launches(
        repo_with_worktrees, monkeypatch):
    (repo_with_worktrees.newer / "state").mkdir()
    sessions = _sessions_exit_with(monkeypatch, 0)

    returncode = branch_session.launch(
        repo_with_worktrees.newer, primary=repo_with_worktrees.primary)

    assert returncode == 0
    assert len(sessions) == 1
    assert not _note_left_in(repo_with_worktrees.newer).exists()


def test_a_session_that_fails_on_a_current_worktree_is_not_blamed_on_its_age(
        repo_with_worktrees, monkeypatch):
    (repo_with_worktrees.newer / "state").mkdir()
    _sessions_exit_with(monkeypatch, 1)

    returncode = branch_session.launch(
        repo_with_worktrees.newer, primary=repo_with_worktrees.primary)

    assert returncode == 1
    assert not _note_left_in(repo_with_worktrees.newer).exists()


@pytest.fixture
def engine_checkouts(tmp_path: Path) -> SimpleNamespace:
    """A stand-in player_core repo at a tag, and a second checkout of it left before
    the tag -- what a worktree pinned by an agent looks like once main moves on."""
    current = tmp_path / "player_core"
    current.mkdir()
    git(current, "-c", "init.defaultBranch=main", "init")
    (current / "pyproject.toml").write_text(
        '[project]\nname = "player-core"\nversion = "0.1.0"\n', encoding="utf-8")
    git(current, "add", "pyproject.toml")
    git(current, "commit", "-m", "The engine", when="2026-01-01T12:00:00")
    before_the_tag = tmp_path / "player_core_before_the_tag"
    git(current, "worktree", "add", "--detach", str(before_the_tag))
    git(current, "commit", "--allow-empty", "-m", "What the branch imports",
        when="2026-02-01T12:00:00")
    git(current, "tag", "v0.1.9")
    return SimpleNamespace(current=current, before_the_tag=before_the_tag)


def _built_against_player_core(worktree: Path, tag: str) -> None:
    (worktree / "pyproject.toml").write_text(
        '[project]\nname = "fun-time"\nversion = "0.1.0"\n'
        f'dependencies = ["player-core @ git+https://github.com/haglio/player_core@{tag}"]\n',
        encoding="utf-8")


def _runs_its_players_out_of(worktree: Path, checkout: Path) -> None:
    (worktree / "state").mkdir(exist_ok=True)
    (worktree / "state" / branch_session.GENAU_DIRS_OVERRIDE_NAME).write_text(
        f"{checkout}\n", encoding="utf-8")


def test_a_shortcut_is_refused_while_a_pinned_player_core_checkout_predates_the_branchs_pin(
        primary_with_launcher, engine_checkouts):
    """The chain has no error state of its own: a stale checkout on the players'
    path runs some other player_core, and the Main Player dies at import on a name
    the branch was built against."""
    _built_against_player_core(primary_with_launcher.newer, "v0.1.9")
    _runs_its_players_out_of(primary_with_launcher.newer, engine_checkouts.before_the_tag)

    with pytest.raises(branch_session.OutOfDateSibling) as refused:
        branch_session.write_launch_shortcut(
            primary_with_launcher.newer, primary=primary_with_launcher.primary)

    assert str(engine_checkouts.before_the_tag) in str(refused.value)
    assert "v0.1.9" in str(refused.value)
    assert branch_session._generated_shortcuts(primary_with_launcher.newer) == {}


def test_the_shortcut_command_reports_a_refusal_without_a_traceback(
        primary_with_launcher, engine_checkouts, monkeypatch, capsys):
    _built_against_player_core(primary_with_launcher.newer, "v0.1.9")
    _runs_its_players_out_of(primary_with_launcher.newer, engine_checkouts.before_the_tag)
    monkeypatch.setattr(branch_session, "primary_checkout",
                        lambda start=None: primary_with_launcher.primary)

    returncode = branch_session.main(["--shortcut", str(primary_with_launcher.newer)])

    assert returncode == 1
    assert "v0.1.9" in capsys.readouterr().err


def test_a_pinned_checkout_carrying_the_branchs_pin_is_current(
        repo_with_worktrees, engine_checkouts):
    _built_against_player_core(repo_with_worktrees.newer, "v0.1.9")
    _runs_its_players_out_of(repo_with_worktrees.newer, engine_checkouts.current)

    assert branch_session.sibling_checkouts_older_than_the_pin(repo_with_worktrees.newer) == []


def test_a_launch_out_of_a_pinned_checkout_from_before_the_branchs_pin_is_refused_with_a_note(
        repo_with_worktrees, engine_checkouts, monkeypatch):
    _built_against_player_core(repo_with_worktrees.newer, "v0.1.9")
    _runs_its_players_out_of(repo_with_worktrees.newer, engine_checkouts.before_the_tag)
    sessions = _sessions_exit_with(monkeypatch, 0)

    returncode = branch_session.launch(
        repo_with_worktrees.newer, primary=repo_with_worktrees.primary)

    assert returncode == 1
    assert sessions == []
    note = _note_left_in(repo_with_worktrees.newer).read_text(encoding="utf-8")
    assert "Fun Time couldn't start on example/newer" in note
    assert "player_core checkout older than the one it was built against" in note


class TestTheSessionOnTheTaskbar:
    """A branch session's button has no pinned shortcut to take its letter, its
    name and its relaunch from, so the session says them itself, once, for every
    window that joins the button -- its own and an app it hosts."""

    def _described(self, monkeypatch, tmp_path, *, branch_session_flag: bool) -> dict:
        if branch_session_flag:
            monkeypatch.setenv(preview_marker.FLAG, "1")
        else:
            monkeypatch.delenv(preview_marker.FLAG, raising=False)
        monkeypatch.setattr(preview_marker, "preview_of", lambda checkout: Preview(feature="a feature"))
        monkeypatch.setattr(preview_marker, "INKED_ICON_FOLDER", tmp_path / "inked")
        described: dict = {}
        monkeypatch.setattr(branch_session, "describe_taskbar_app",
                            lambda app_id, app: described.update({app_id: app}))
        branch_session.describe_the_session_on_the_taskbar(
            tmp_path / "fun_time" / ".claude" / "worktrees" / "a-feature")
        return described

    def test_a_branch_session_names_its_button_and_inks_its_letter(self, monkeypatch, tmp_path):
        (app_id, app), = self._described(monkeypatch, tmp_path, branch_session_flag=True).items()

        assert app_id == f"{APP_USER_MODEL_ID}.Preview"
        assert app.name == "Fun Time \u2014 preview of a feature"
        assert app.icon.parent == tmp_path / "inked"

    def test_its_button_starts_the_same_branch_again(self, monkeypatch, tmp_path):
        (app,) = self._described(monkeypatch, tmp_path, branch_session_flag=True).values()

        assert app.relaunch == subprocess.list2cmdline([
            "wscript.exe", str(tmp_path / "fun_time" / "launch_branch.vbs"),
            str(tmp_path / "fun_time" / ".claude" / "worktrees" / "a-feature")])

    def test_the_live_session_leaves_its_button_to_the_pinned_shortcut(self, monkeypatch, tmp_path):
        assert self._described(monkeypatch, tmp_path, branch_session_flag=False) == {}
