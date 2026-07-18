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


def test_shell_wrapper_starts_the_orchestrator_on_the_project_venv():
    """``main.sh`` is the documented alternative launch, and it reached for
    ``python`` then ``py -3`` from PATH — the same miss, from the other door."""
    text = _text("main.sh")

    assert ".venv/Scripts/python.exe" in text
    assert "command -v python" not in text
    assert "py -3" not in text
