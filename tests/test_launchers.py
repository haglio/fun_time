"""The ways a session is started: the Windows launchers and the shell wrapper.

Both must run the orchestrator on the project venv.  ``fun_time`` imports its
sibling packages — ``app_support``, ``player_core`` — and those are editable
installs that exist only in ``.venv``.  A python taken from PATH finds the
sibling *repo* directories as namespace packages instead and dies on
``No module named 'player_core.playlist'`` while importing, which is before the
orchestrator has configured any logging: the app never appears and no log
anywhere says why.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _text(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


def test_windows_launcher_starts_the_orchestrator_on_the_project_venv():
    text = _text("launch.vbs")

    assert ".venv\\Scripts\\python.exe" in text
    # No PATH search to fall back to — falling back is the failure.
    assert "where " not in text


def test_windows_launcher_keeps_what_the_orchestrator_wrote_to_its_console():
    """A launch that dies during import dies before any log file exists, and the
    launcher runs it in a hidden window — so the traceback went nowhere and the
    app just never appeared.  Redirecting the console to ``state/launcher.log``
    is what makes the next one readable."""
    text = _text("launch.vbs")

    assert "launcher.log" in text
    assert "2>&1" in text


def test_windows_launcher_watches_for_the_orchestrators_ready_signal():
    """The orchestrator drops ``launcher.ready`` once it has committed to run.
    The hidden launch has no other way to know it got off the ground, so it
    waits for that file — its absence is what a silent crash looks like."""
    text = _text("launch.vbs")

    assert "launcher.ready" in text


def test_windows_launcher_learns_at_once_when_the_child_exits():
    """A crash tears the child down in a second; polling the ready file alone
    would make the user wait out the whole timeout. cmd stamps
    ``launcher.exited`` the moment the child returns, so the launcher reacts to
    a crash immediately instead."""
    text = _text("launch.vbs")

    assert "launcher.exited" in text
    # The stamp must run whichever way the child exits — an unconditional ``&``,
    # not ``&&`` which would skip it after a non-zero (crashing) exit.
    assert "2>&1 & type nul >" in text


def test_windows_launcher_surfaces_a_failed_start_with_the_log():
    """The whole point: a launch that never appears must say so, and point at
    the log that says why, instead of leaving the user staring at nothing."""
    text = _text("launch.vbs")

    assert "failed to start" in text
    # A second critical MsgBox beyond the missing-venv one, and it shows the
    # log's tail rather than only naming the file.
    assert text.count("MsgBox") >= 2
    assert "launchLog" in text
    assert "vbCritical" in text


def test_shell_wrapper_starts_the_orchestrator_on_the_project_venv():
    """``main.sh`` is the documented alternative launch, and it reached for
    ``python`` then ``py -3`` from PATH — the same miss, from the other door."""
    text = _text("main.sh")

    assert ".venv/Scripts/python.exe" in text
    assert "command -v python" not in text
    assert "py -3" not in text


def test_vr_launcher_holds_the_same_invariants_with_its_own_sentinels():
    """launch_vr.vbs is launch.vbs aimed at fun_time_vr.orchestrator.  It keeps
    every hidden-launch safeguard — the venv pin, the console log, the
    ready/exited sentinel pair, the failure dialog with the log tail — under
    VR-specific names, so a desktop launch's leftovers can never vouch for a
    VR launch or the other way around."""
    text = _text("launch_vr.vbs")

    assert ".venv\\Scripts\\python.exe" in text
    assert "where " not in text
    assert "-m fun_time_vr.orchestrator" in text
    assert "vr_launcher.log" in text
    assert "2>&1 & type nul >" in text
    assert "vr_launcher.ready" in text
    assert "vr_launcher.exited" in text
    assert "failed to start" in text
    assert text.count("MsgBox") >= 2
    assert "vbCritical" in text
    # The sentinel names must not collide with the desktop launcher's.
    desktop = _text("launch.vbs")
    assert "vr_launcher.ready" not in desktop


def test_branch_launcher_holds_the_same_invariants_and_runs_from_the_primary():
    """``launch_branch.vbs`` is launch.vbs aimed at a worktree.  It keeps every
    hidden-launch safeguard — the venv pin, the console log, the ready/exited
    sentinel pair, the failure dialog with the log tail — and it runs
    ``fun_time.branch_session`` out of the primary checkout, because the
    launcher and the config it writes are main's code.  Only the session
    underneath is the branch's; branch_session is what moves the working
    directory into the worktree."""
    text = _text("launch_branch.vbs")

    assert ".venv\\Scripts\\python.exe" in text
    assert "where " not in text
    assert "-m fun_time.branch_session" in text
    assert "launcher.log" in text
    assert "2>&1 & type nul >" in text
    assert "launcher.ready" in text
    assert "launcher.exited" in text
    assert "failed to start" in text
    assert text.count("MsgBox") >= 2
    assert "vbCritical" in text


def test_branch_launcher_watches_the_worktrees_sentinels_not_the_primarys():
    """It reuses launch.vbs's sentinel names, kept apart by *directory*: the
    branch session's state dir is inside the worktree, so that is where it
    writes ``launcher.ready`` and where the launcher must look.  Watching the
    the primary checkout's would let the live session's leftovers vouch for a branch launch
    that never got off the ground."""
    text = _text("launch_branch.vbs")

    assert 'stateDir = fso.BuildPath(worktree, "state")' in text
    assert 'readyFile = fso.BuildPath(stateDir, "launcher.ready")' in text
    assert 'exitedFlag = fso.BuildPath(stateDir, "launcher.exited")' in text
    assert 'launchLog = fso.BuildPath(stateDir, "launcher.log")' in text


def test_branch_launcher_takes_the_worktree_from_the_shortcut_that_ran_it():
    """He is never asked to find a branch.  The agent that has something to show
    makes a ``Verify <branch>.lnk`` naming its worktree, and the launcher reads
    it from there — so double-clicking this file directly has nothing to run,
    and says so rather than doing something arbitrary."""
    text = _text("launch_branch.vbs")

    assert "WScript.Arguments.Count < 1" in text
    assert "worktree = WScript.Arguments(0)" in text
    assert "Double-click that instead." in text
    # No menu to work through: picking is the agent's job, not his.
    assert "InputBox" not in text


def test_branch_launcher_says_so_when_the_worktree_has_been_deleted():
    """A shortcut outlives the branch it was made for — the worktree goes when
    the work lands.  Without this the launch dies inside python with a config
    error, and the dialog he gets says nothing about why."""
    text = _text("launch_branch.vbs")

    assert "fso.FolderExists(worktree)" in text
    assert "already in Fun Time" in text


def test_windows_launcher_runs_the_orchestrator_under_a_name_that_says_fun_time():
    """Task Manager identifies a process by its image name and nothing else, so
    an orchestrator started through a plain ``python.exe`` is one anonymous row
    among the user's other Python apps — and when a session strands its children
    (the orchestrator dies without reaping, leaving no window to close), the task
    list is the only way back and cannot say which rows are safe to end.

    The children get named by ``fun_time.process_identity``.  This is the one
    process that module cannot name, because it is the process that would import
    it, so the launcher makes the copy itself."""
    text = _text("launch.vbs")

    assert r'namedExe = fso.BuildPath(scriptDir, ".venv\Scripts\FunTime-Orchestrator.exe")' in text
    assert "fso.CopyFile pythonExe, namedExe, True" in text
    # Refreshed when the interpreter it was copied from changes, or a Python
    # upgrade leaves the session running an interpreter nobody installed.
    assert "fso.GetFile(namedExe).Size <> fso.GetFile(pythonExe).Size" in text


def test_windows_launcher_still_launches_when_it_cannot_make_that_copy():
    """A read-only venv or an antivirus hold must cost the name, not the app.
    Every failure path leaves ``pythonExe`` as it was, and the session starts
    anonymous exactly as it did before."""
    text = _text("launch.vbs")

    assert "On Error Resume Next" in text
    assert "If Err.Number = 0 And fso.FileExists(namedExe) Then pythonExe = namedExe" in text
    assert "On Error Goto 0" in text


def test_the_launchers_compile():
    """A syntax error in a launcher is an app that cannot start at all, and no
    text assertion above would catch one.

    Checked by handing the script to cscript with ``WScript.Quit 0`` prepended:
    VBScript compiles a whole file before it runs any of it, so a syntax error
    anywhere still fails here — while the guard means a clean file exits before
    executing a single statement, and so never launches a session."""
    for name in ("launch.vbs", "launch_branch.vbs", "launch_vr.vbs"):
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / name
            probe.write_text("WScript.Quit 0\r\n" + _text(name), encoding="utf-8")
            result = subprocess.run(
                ["cscript.exe", "//Nologo", str(probe)],
                capture_output=True, text=True, check=False,
            )
            assert result.returncode == 0, f"{name} does not compile:\n{result.stdout}{result.stderr}"
