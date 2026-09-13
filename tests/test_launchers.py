"""The ways a session is started: the Windows launchers and the shell wrapper.

Both must run the orchestrator on the project venv.  ``fun_time`` imports its
sibling packages — ``app_support``, ``player_core`` — and those are editable
installs that exist only in ``.venv``.  A python taken from PATH finds the
sibling *repo* directories as namespace packages instead and dies on
``No module named 'player_core.playlist'`` while importing, which is before the
orchestrator has configured any logging: the app never appears and no log
anywhere says why.

The three ``.vbs`` launchers are rendered from their specs in pyproject.toml by
``app_support.launcher``, whose own tests hold what every launcher does: the venv
pin, a log a stray child still holding it cannot take from a launch, the ready
and exit files a hidden launch reports through, and the failure dialog showing
the end of the log.  What is Fun Time's is asked of each launcher under the real
script host.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from app_support.launcher import (
    assert_launchers_match_their_specs,
    dry_run,
    run_under_script_host,
)

from fun_time.branch_session import LAUNCHER_NAME, VR_LAUNCH_FLAG
from fun_time.orchestrator import STARTUP_MARKER_NAME
from fun_time_vr.orchestrator import VR_STARTUP_MARKER_NAME

PROJECT_DIR = Path(__file__).resolve().parent.parent
STATE = PROJECT_DIR / "state"

on_windows = pytest.mark.skipif(sys.platform != "win32", reason="the Windows script host")


def test_the_launchers_are_where_their_shortcuts_point():
    for name in ("launch.vbs", "launch_vr.vbs", LAUNCHER_NAME):
        assert (PROJECT_DIR / name).is_file(), name


def test_the_launchers_are_what_their_specs_render():
    assert_launchers_match_their_specs(PROJECT_DIR)


def test_shell_wrapper_starts_the_orchestrator_on_the_project_venv():
    """``main.sh`` is the documented alternative launch, and it reached for
    ``python`` then ``py -3`` from PATH — the same miss, from the other door."""
    text = (PROJECT_DIR / "main.sh").read_text(encoding="utf-8")

    assert ".venv/Scripts/python.exe" in text
    assert "command -v python" not in text
    assert "py -3" not in text


@on_windows
def test_the_desktop_launcher_runs_the_orchestrator_and_watches_the_marker_it_writes():
    report = dry_run(PROJECT_DIR / "launch.vbs")

    assert Path(report.value("interpreter")).parent == PROJECT_DIR / ".venv" / "Scripts"
    assert Path(report.value("directory")) == PROJECT_DIR
    assert report.value("arguments") == "-m fun_time.orchestrator"
    assert Path(report.value("log")) == STATE / "launcher.log"
    assert Path(report.value("ready")) == STATE / STARTUP_MARKER_NAME


@on_windows
def test_the_vr_launcher_runs_the_vr_orchestrator_under_its_own_marker():
    """So a desktop launch's leftovers can never vouch for a VR launch, nor the
    other way around."""
    report = dry_run(PROJECT_DIR / "launch_vr.vbs")

    assert report.value("arguments") == "-m fun_time_vr.orchestrator"
    assert Path(report.value("log")) == STATE / "vr_launcher.log"
    assert Path(report.value("ready")) == STATE / VR_STARTUP_MARKER_NAME


@on_windows
def test_a_branch_launch_runs_branch_session_from_the_primary_and_watches_the_worktree(
    tmp_path: Path,
):
    """The launcher and the config it writes are main's code, run from the
    primary; only the session underneath is the branch's, and it reports in the
    worktree's state folder -- watching the primary's would let the live
    session's leftovers vouch for a branch launch that never got off the ground."""
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    report = dry_run(PROJECT_DIR / LAUNCHER_NAME, str(worktree), "example-branch")

    assert Path(report.value("directory")) == PROJECT_DIR
    assert Path(report.value("interpreter")).parent == PROJECT_DIR / ".venv" / "Scripts"
    assert report.value("arguments") == f'-m fun_time.branch_session "{worktree}"'
    assert report.value("label") == "example-branch"
    assert Path(report.value("ready")) == worktree / "state" / STARTUP_MARKER_NAME


@on_windows
def test_a_vr_branch_launch_asks_for_the_headset_and_watches_the_vr_marker(tmp_path: Path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    report = dry_run(PROJECT_DIR / LAUNCHER_NAME, str(worktree), "example-branch",
                     VR_LAUNCH_FLAG)

    assert report.value("app") == "Fun Time VR"
    assert report.value("arguments") == (
        f'-m fun_time.branch_session "{worktree}" {VR_LAUNCH_FLAG}')
    assert Path(report.value("ready")) == worktree / "state" / VR_STARTUP_MARKER_NAME


@on_windows
def test_the_branch_launcher_on_its_own_says_what_to_double_click_instead():
    """He is never asked to find a branch: the agent with something to show
    leaves a Verify shortcut naming its worktree."""
    run = run_under_script_host(PROJECT_DIR / LAUNCHER_NAME)

    assert run.returncode == 1
    assert "Double-click that instead." in run.output


@on_windows
def test_a_shortcut_that_outlived_its_worktree_says_the_work_is_already_in_fun_time(
    tmp_path: Path,
):
    run = run_under_script_host(PROJECT_DIR / LAUNCHER_NAME, str(tmp_path / "gone"),
                                "example-branch")

    assert run.returncode == 1
    assert "already in Fun Time" in run.output
