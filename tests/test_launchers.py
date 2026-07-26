"""The two ways a session is started: the Windows launcher and the shell wrapper.

Both must run the orchestrator on the project venv.  ``fun_time`` imports its
sibling packages — ``app_support``, ``player_core`` — and those are editable
installs that exist only in ``.venv``.  A python taken from PATH finds the
sibling *repo* directories as namespace packages instead and dies on
``No module named 'player_core.playlist'`` while importing, which is before the
orchestrator has configured any logging: the app never appears and no log
anywhere says why.
"""
from __future__ import annotations

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
