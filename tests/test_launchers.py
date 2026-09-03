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

import pytest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _text(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


# The three Windows launchers share one set of hidden-launch safeguards,
# differing only in the sentinel/log names each watches.  Asserted once,
# parametrized — the same block used to be hand-written three times and free
# to drift apart.
_LAUNCHER_SENTINELS = {
    "launch.vbs": ("launcher.log", "launcher.ready", "launcher.exited"),
    "launch_vr.vbs": ("vr_launcher.log", "vr_launcher.ready", "vr_launcher.exited"),
    "launch_branch.vbs": ("launcher.log", "launcher.ready", "launcher.exited"),
}


@pytest.mark.parametrize("name", sorted(_LAUNCHER_SENTINELS))
def test_every_launcher_holds_the_hidden_launch_safeguards(name: str):
    """Each launcher runs its target hidden, so every safeguard matters the
    same way in all three.  The venv pin (a PATH python resolves the sibling
    repos as namespace packages and dies before logging exists); the console
    redirect that keeps the import-time traceback; the ready sentinel (its
    absence is what a silent crash looks like) and the exited stamp (cmd
    writes it the moment the child returns, unconditionally — ``&``, never
    ``&&`` — so a crash is reacted to at once instead of waiting out the
    timeout); and the failure dialog that shows the log's tail."""
    log, ready, exited = _LAUNCHER_SENTINELS[name]
    text = _text(name)

    assert ".venv\\Scripts\\python.exe" in text
    assert "where " not in text  # no PATH search to fall back to
    assert log in text
    assert ready in text
    assert exited in text
    assert "2>&1 & type nul >" in text
    assert "failed to start" in text
    assert text.count("MsgBox") >= 2
    assert "launchLog" in text  # the dialog shows the tail, not just the name
    assert "vbCritical" in text


def test_shell_wrapper_starts_the_orchestrator_on_the_project_venv():
    """``main.sh`` is the documented alternative launch, and it reached for
    ``python`` then ``py -3`` from PATH — the same miss, from the other door."""
    text = _text("main.sh")

    assert ".venv/Scripts/python.exe" in text
    assert "command -v python" not in text
    assert "py -3" not in text


def test_vr_launcher_aims_at_the_vr_orchestrator_under_its_own_sentinels():
    """launch_vr.vbs is launch.vbs aimed at fun_time_vr.orchestrator, under
    VR-specific sentinel names, so a desktop launch's leftovers can never
    vouch for a VR launch or the other way around.  (The shared safeguards
    are the parametrized test above.)"""
    text = _text("launch_vr.vbs")

    assert "-m fun_time_vr.orchestrator" in text
    # The sentinel names must not collide with the desktop launcher's.
    desktop = _text("launch.vbs")
    assert "vr_launcher.ready" not in desktop


def test_branch_launcher_runs_branch_session_from_the_primary():
    """``launch_branch.vbs`` is launch.vbs aimed at a worktree: it runs
    ``fun_time.branch_session`` out of the primary checkout, because the
    launcher and the config it writes are main's code.  Only the session
    underneath is the branch's; branch_session is what moves the working
    directory into the worktree.  (The shared safeguards are the parametrized
    test above.)"""
    text = _text("launch_branch.vbs")

    assert "-m fun_time.branch_session" in text


def test_branch_launcher_watches_the_worktrees_sentinels_not_the_primarys():
    """It reuses the desktop launcher's sentinel names, kept apart by
    *directory*: the branch session's state dir is inside the worktree, so that
    is where it writes ``launcher.ready`` and where the launcher must look.
    Watching the primary checkout's would let the live session's leftovers vouch
    for a branch launch that never got off the ground.  The three names are
    built from one stem so a VR branch watches its own trio rather than the
    desktop's."""
    text = _text("launch_branch.vbs")

    assert 'stateDir = fso.BuildPath(worktree, "state")' in text
    for name in ("launcher", "vr_launcher"):
        assert f'readyFile = fso.BuildPath(stateDir, "{name}.ready")' in text
        assert f'exitedFlag = fso.BuildPath(stateDir, "{name}.exited")' in text
        assert f'launchLog = fso.BuildPath(stateDir, "{name}.log")' in text


def test_branch_launcher_aims_at_the_headset_when_the_shortcut_says_so():
    """The VR shortcut passes one more argument, and that is the whole
    difference: the same launcher, the same worktree, ``--vr`` through to
    branch_session -- which swaps the entry point -- and the VR trio of
    sentinels, because FunTimeVR's orchestrator writes ``vr_launcher.ready``
    rather than the desktop one's marker.  Watching the wrong one would pop the
    failure dialog over a session that had started perfectly well."""
    text = _text("launch_branch.vbs")

    assert '"--vr"' in text
    assert 'fso.BuildPath(stateDir, "vr_launcher.ready")' in text
    assert "Fun Time VR" in text


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
    """Windows identifies a process by its image name and by its version
    resource's description, and a plain ``python.exe`` supplies "python.exe" and
    "Python" — so an orchestrator started through one is an anonymous row among
    the user's other Python apps, and when a session strands its children (the
    orchestrator dies without reaping, leaving no window to close) the task list
    is the only way back and cannot say which rows are safe to end.

    The children are named as ``fun_time.process_identity`` launches them.  The
    orchestrator cannot be, because writing the copy takes the interpreter being
    launched — so the launcher picks it up when a previous session left one."""
    text = _text("launch.vbs")

    assert r'namedExe = fso.BuildPath(scriptDir, ".venv\Scripts\FunTime-Orchestrator.exe")' in text
    assert "If fso.FileExists(namedExe) Then pythonExe = namedExe" in text


def test_windows_launcher_still_launches_before_any_session_has_named_it():
    """The naming runs one launch behind, so a checkout that has never run has
    no copy to find.  That must cost the name and nothing else: the launcher
    falls through to the venv interpreter it always used."""
    text = _text("launch.vbs")

    assert r'pythonExe = fso.BuildPath(scriptDir, ".venv\Scripts\python.exe")' in text
    # No copying here — the launcher only ever consumes what a session left.
    assert "CopyFile" not in text


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
