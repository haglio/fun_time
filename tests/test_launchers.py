from __future__ import annotations

import sys
from pathlib import Path

import pytest
from app_support.launcher import dry_run, run_under_script_host
from app_support.production_update import UPKEEP_MARK

from fun_time.branch_session import (
    LAUNCHER_NAME,
    OUT_OF_DATE_NOTE_NAME,
    VR_LAUNCH_FLAG,
)
from fun_time.orchestrator import STARTUP_MARKER_NAME
from fun_time_vr.orchestrator import VR_STARTUP_MARKER_NAME

PROJECT_DIR = Path(__file__).resolve().parent.parent
STATE = PROJECT_DIR / "state"

on_windows = pytest.mark.skipif(sys.platform != "win32", reason="the Windows script host")


def test_main_sh_never_runs_a_path_python_that_dies_before_any_log():
    text = (PROJECT_DIR / "main.sh").read_text(encoding="utf-8")

    assert ".venv/Scripts/python.exe" in text
    assert "command -v python" not in text
    assert "py -3" not in text


@on_windows
def test_the_desktop_launcher_runs_the_orchestrator_and_watches_the_marker_it_writes():
    report = dry_run(PROJECT_DIR / "launch.vbs")

    assert report.value("arguments") == "-m fun_time.orchestrator"
    assert Path(report.value("ready")) == STATE / STARTUP_MARKER_NAME


@on_windows
def test_the_vr_launcher_runs_the_vr_orchestrator_under_its_own_marker():
    report = dry_run(PROJECT_DIR / "launch_vr.vbs")

    assert report.value("arguments") == "-m fun_time_vr.orchestrator"
    assert Path(report.value("ready")) == STATE / VR_STARTUP_MARKER_NAME


@on_windows
def test_neither_launchers_leftover_marker_can_vouch_for_the_other_kind_of_launch():
    desktop, headset = dry_run(PROJECT_DIR / "launch.vbs"), dry_run(PROJECT_DIR / "launch_vr.vbs")

    assert Path(desktop.value("ready")) != Path(headset.value("ready"))


@on_windows
def test_a_branch_launch_runs_branch_session_from_the_primary(tmp_path: Path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    report = dry_run(PROJECT_DIR / LAUNCHER_NAME, str(worktree), "example-branch")

    assert Path(report.value("directory")) == PROJECT_DIR
    assert Path(report.value("interpreter")).parent == PROJECT_DIR / ".venv" / "Scripts"
    assert report.value("arguments") == f'-m fun_time.branch_session "{worktree}"'
    assert report.value("label") == "example-branch"


@on_windows
def test_the_live_sessions_leftover_marker_cannot_vouch_for_a_branch_launch(tmp_path: Path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    report = dry_run(PROJECT_DIR / LAUNCHER_NAME, str(worktree), "example-branch")

    assert Path(report.value("ready")) == worktree / "state" / STARTUP_MARKER_NAME
    assert Path(report.value("ready")) != STATE / STARTUP_MARKER_NAME


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
def test_a_branch_launch_reads_the_note_branch_session_leaves_where_it_leaves_it(
    tmp_path: Path,
):
    worktree = tmp_path / "worktree"
    worktree.mkdir()

    report = dry_run(PROJECT_DIR / LAUNCHER_NAME, str(worktree), "example-branch")

    assert Path(report.value("note")) == worktree / "state" / OUT_OF_DATE_NOTE_NAME


@on_windows
def test_the_branch_launcher_on_its_own_says_what_to_double_click_instead():
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


@on_windows
def test_the_main_verifier_is_the_machines_upkeep_so_no_update_waits_for_it():
    report = dry_run(PROJECT_DIR / "launch_main_verifier.vbs")

    assert report.values("environment") == [f"{UPKEEP_MARK}=1"]


