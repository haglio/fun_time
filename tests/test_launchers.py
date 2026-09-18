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

import contextlib
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from fun_time.branch_session import OUT_OF_DATE_NOTE_NAME

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _text(name: str) -> str:
    return (PROJECT_DIR / name).read_text(encoding="utf-8")


# The three Windows launchers share one set of hidden-launch safeguards,
# differing only in the sentinel/log names each watches.  Asserted once,
# parametrized — the same block used to be hand-written three times and free
# to drift apart.
_LAUNCHER_SENTINELS = {
    "launch.vbs": ("launcher", "launcher.ready", "launcher.exited"),
    "launch_vr.vbs": ("vr_launcher", "vr_launcher.ready", "vr_launcher.exited"),
    "launch_branch.vbs": ("launcher", "launcher.ready", "launcher.exited"),
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
    stem, ready, exited = _LAUNCHER_SENTINELS[name]
    text = _text(name)

    assert ".venv\\Scripts\\python.exe" in text
    assert "where " not in text  # no PATH search to fall back to
    assert f'LaunchLogIn(stateDir, "{stem}")' in text
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
        assert f'launchLog = LaunchLogIn(stateDir, "{name}")' in text


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


def test_branch_launcher_says_when_the_branchs_copy_is_behind_the_apps():
    """A launcher made from a copy that has since gone out of date dies inside
    python, on whatever main changed under it — a config key its loader has
    never heard of, a moved module — and the dialog he gets is a traceback about
    something he did not do.  branch_session leaves a note saying which branch
    and how far out of date; the launcher shows that instead of the log tail,
    and clears a previous launch's note first so a stale one cannot explain a
    failure it had nothing to do with."""
    text = _text("launch_branch.vbs")

    assert f'outOfDateNote = fso.BuildPath(stateDir, "{OUT_OF_DATE_NOTE_NAME}")' in text
    assert "If fso.FileExists(outOfDateNote) Then fso.DeleteFile outOfDateNote" in text
    assert "If Len(outOfDate) > 0 Then" in text


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
    """The naming runs one launch late, so a checkout that has never run has
    no copy to find.  That must cost the name and nothing else: the launcher
    falls through to the venv interpreter it always used."""
    text = _text("launch.vbs")

    assert r'pythonExe = fso.BuildPath(scriptDir, ".venv\Scripts\python.exe")' in text
    # No copying here — the launcher only ever consumes what a session left.
    assert "CopyFile" not in text


# The launch log is the one file a launch cannot do without, and the one thing
# that can already be held when a launch starts: see tests/test_child_output.py
# for how a stray child of a dead session comes to hold it, and what it cost.
_LAUNCH_LOG_STEMS = {
    "launch.vbs": "launcher",
    "launch_vr.vbs": "vr_launcher",
    "launch_branch.vbs": "launcher",
}


@pytest.mark.parametrize("name", sorted(_LAUNCH_LOG_STEMS))
def test_every_launcher_appends_to_its_log_rather_than_erasing_it(name: str):
    """The failed launch's traceback is the only record of why a click did
    nothing, and the retry that follows seconds later used to erase it: the
    redirect truncated.  Appending keeps both, and the banner LaunchLogIn
    stamps is what tells them apart."""
    text = _text(name)

    appending = '>> """ & launchLog'
    assert appending in text
    assert '" > """ & launchLog' not in text  # nothing truncating it
    assert "LaunchLogIn(stateDir" in text


@pytest.mark.parametrize("name", sorted(_LAUNCH_LOG_STEMS))
def test_a_held_log_costs_a_name_and_not_the_launch(name: str, tmp_path: Path):
    """A child of an earlier session that outlived it holds the launch log, and
    Windows lets nobody else write it.  Redirecting into it anyway fails inside
    cmd, before python runs: no window, no log line anywhere, nothing to see at
    all -- the launch that takes two clicks.  So the launcher takes the next
    free name instead."""
    stem = _LAUNCH_LOG_STEMS[name]
    held = tmp_path / f"{stem}.log"

    with _held_by_a_stray_child(held):
        chosen = Path(_launch_log_chosen_by(name, tmp_path, stem))
        assert chosen.name == f"{stem}-2.log"

    assert Path(_launch_log_chosen_by(name, tmp_path, stem)).name == f"{stem}.log"


@contextlib.contextmanager
def _held_by_a_stray_child(log: Path):
    """Hold *log* the way cmd's redirect does -- exclusively, from another
    process -- for the length of the block.

    Both ends of the hold are asserted, and the hold itself outlasts anything
    asked under it by minutes.  On a machine carrying several suites at once,
    starting the stray and asking a launcher each take seconds, and a wait that
    merely gave up left the log free with the block still calling it held --
    which reads as the launcher choosing the wrong name."""
    stray = subprocess.Popen(
        f'cmd /c ping -n 300 127.0.0.1 > "{log}"',
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        _wait_until(lambda: log.exists() and not _can_append(log),
                    what=f"the stray child to take {log.name}")
        yield
    finally:
        # The tree, not the one process: killing cmd alone leaves ping holding
        # the inherited handle, which is the whole phenomenon under test.
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(stray.pid)],
            capture_output=True, check=False,
        )
        stray.wait(timeout=30)
        _wait_until(lambda: _can_append(log), what=f"{log.name} to come free again")


def _can_append(path: Path) -> bool:
    try:
        path.open("ab").close()
    except OSError:
        return False
    return True


def _wait_until(condition, *, what: str, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError(f"Waited {timeout_s:g}s for {what}, and it never happened")


def _launch_log_chosen_by(name: str, state_dir: Path, stem: str) -> str:
    """The log the launcher would redirect into, asked of the launcher itself.

    VBScript hoists its functions, so a driver prepended to the real file can
    call LaunchLogIn and quit before a single one of the launcher's own
    statements runs -- the same trick the compile check above uses."""
    driver = "\r\n".join([
        'Set fso = CreateObject("Scripting.FileSystemObject")',
        "WScript.Echo LaunchLogIn(WScript.Arguments(0), WScript.Arguments(1))",
        "WScript.Quit 0",
        "",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / name
        probe.write_text(driver + _text(name), encoding="utf-8")
        result = subprocess.run(
            ["cscript.exe", "//Nologo", str(probe), str(state_dir), stem],
            capture_output=True, text=True, check=False,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


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
