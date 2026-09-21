"""What every child a session launches has to say for itself.

Two promises, both of them about what happens on the user's screen when the
session is not the one the launch was written against, and both swept for
below over every spawn in the tree.

**Where its output goes.** ``launch.vbs`` runs the orchestrator through
``cmd /c ... >> launcher.log``, so ``cmd`` owns that file for as long as the
session lasts and Windows lets nobody else write it meanwhile.  A ``Popen`` that
names no ``stdout``/``stderr`` does not send the child's output nowhere -- it
hands the child *this process's* pair, which is that same handle.  Most children
die with the session and give it back.  Some do not: Chrome opened for the
Random Favs Browser, the broker tray (whose own docstring says it must outlive
the run), an Origenerator kept across a crossing to the headset.  One of those
goes on holding ``launcher.log`` for as long as its own window is open, which
can be hours.

Then the next launch redirects into the held file, and ``cmd`` fails on the
redirect -- before it runs python at all.  Nothing starts.  ``orchestrator.log``
gets no line, because logging is configured inside the interpreter that never
ran; the single-instance mutex says nothing, for the same reason; and the
launcher's own dialog, if it is seen at all, shows the tail of a log written by
a session that ended long ago.  The click does nothing, and the app starts on a
later click only because the stray has finally gone.

**That it opens no console window.** A session ``launch.vbs`` started runs
under the console interpreter and has a console of its own, which the launcher
hides -- so a console child inherits somewhere invisible to put a console.  A
session the crossing relay started runs under the WINDOWED interpreter and has
no console at all, and Windows answers a console child of a console-less parent
by giving it a console window, on screen.  Every VR session is one of those, and
its teardown kills its children one at a time through ``taskkill``: a black
window per kill, which is what quitting FunTimeVR flashed.  Which kind of
session a launch lands in is not something the call site can know, so every
child says for itself that it wants no console.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from fun_time.child_launch import no_child_log, no_console_window, open_child_log

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = (ROOT / "fun_time", ROOT / "satellite", ROOT / "fun_time_vr", ROOT / "main_player")

# The one helper that supplies the pair by unpacking.  A call may instead name
# stdout and stderr itself; anything else is the inherited default.
_SUPPLIES_THE_PAIR = "no_child_log"

# Every helper that carries CREATE_NO_WINDOW: this repo's two, and the shared
# one they are built over.  A new helper belongs here the day it is written --
# the sweep asks by name, because a kwargs helper that sets no creationflags
# looks exactly like one that does.
_SUPPRESSES_THE_CONSOLE = frozenset({
    "no_console_window",          # a child that opens windows of its own
    "hidden_subprocess_kwargs",   # a console tool, hidden altogether
    "subprocess_window_kwargs",   # this repo's name for that one
    "broker_launch_kwargs",       # that one, plus breaking out of our job
    "origenerator_launch_kwargs",
})

_SPAWNS = frozenset({"Popen", "run", "check_output"})


def _spawn_calls() -> list[tuple[Path, ast.Call]]:
    found = []
    for path in sorted(p for pkg in PACKAGES for p in pkg.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_a_spawn(node.func):
                found.append((path, node))
    return found


def _is_a_spawn(func: ast.expr) -> bool:
    return (isinstance(func, ast.Attribute) and func.attr in _SPAWNS
            and isinstance(func.value, ast.Name) and func.value.id == "subprocess")


def _popen_calls() -> list[tuple[Path, ast.Call]]:
    return [(path, call) for path, call in _spawn_calls() if call.func.attr == "Popen"]


def _unpacks(call: ast.Call, names: frozenset[str] | set[str]) -> bool:
    return any(
        keyword.arg is None
        and isinstance(keyword.value, ast.Call)
        and isinstance(keyword.value.func, ast.Name)
        and keyword.value.func.id in names
        for keyword in call.keywords
    )


def _says_where_the_output_goes(call: ast.Call) -> bool:
    named = {keyword.arg for keyword in call.keywords}
    if "stdout" in named and "stderr" in named:
        return True
    return _unpacks(call, {_SUPPLIES_THE_PAIR})


def _asks_for_no_console(call: ast.Call) -> bool:
    if any(keyword.arg == "creationflags" for keyword in call.keywords):
        return True
    if _unpacks(call, _SUPPRESSES_THE_CONSOLE):
        return True
    # Kwargs the caller built and handed down -- the promise is theirs to keep,
    # and the call that built them is swept in its own right.
    return any(keyword.arg is None and isinstance(keyword.value, ast.Name)
               for keyword in call.keywords)


def test_there_are_spawns_to_check():
    """The sweeps below pass vacuously if the scan stops finding them."""
    assert len(_popen_calls()) >= 15
    assert len(_spawn_calls()) >= 20


@pytest.mark.parametrize(
    ("path", "call"),
    [pytest.param(path, call, id=f"{path.name}:{call.lineno}")
     for path, call in _popen_calls()],
)
def test_every_child_says_where_its_output_goes(path: Path, call: ast.Call):
    assert _says_where_the_output_goes(call), (
        f"{path.relative_to(ROOT)}:{call.lineno}: Popen with no stdout/stderr. "
        "An unset pair is INHERITED, and what this process inherited is the "
        "launcher's redirect on state/launcher.log -- a child that outlives the "
        "session then blocks the next launch outright. Pass **no_child_log(), "
        "or a log of the child's own."
    )


@pytest.mark.parametrize(
    ("path", "call"),
    [pytest.param(path, call, id=f"{path.name}:{call.lineno}")
     for path, call in _spawn_calls()],
)
def test_every_child_asks_for_no_console_window(path: Path, call: ast.Call):
    assert _asks_for_no_console(call), (
        f"{path.relative_to(ROOT)}:{call.lineno}: spawn with no creationflags. "
        "A session the crossing relay started runs under the windowed "
        "interpreter and has no console, so Windows gives a console child one "
        "of its own -- a window on the user's screen. Pass "
        "**hidden_subprocess_kwargs() for a console tool, or "
        "**no_console_window() for a child that opens windows of its own."
    )


def test_the_helper_sends_the_pair_to_devnull():
    assert no_child_log() == {
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def test_the_console_helper_carries_the_flag_and_nothing_else():
    """No ``startupinfo``: the hiding one would hide the child's own first
    window, and a cover nobody can see covers nothing."""
    assert no_console_window() == {"creationflags": subprocess.CREATE_NO_WINDOW}


class TestOpenChildLog:
    def test_banner_names_the_launch_and_its_argv(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"

        with open_child_log(log_file, ["pythonw.exe", "-m", "satellite", "--title", "Portrait"]):
            pass

        banner = log_file.read_text(encoding="utf-8")
        assert "pythonw.exe -m satellite --title Portrait" in banner

    def test_appends_so_a_prior_session_survives_the_next_launch(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        log_file.write_text("Traceback from the session that died\n", encoding="utf-8")

        with open_child_log(log_file, ["pythonw.exe"]):
            pass

        assert "Traceback from the session that died" in log_file.read_text(encoding="utf-8")

    def test_rolls_the_log_aside_once_it_passes_the_cap(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        log_file.write_text("x" * 200, encoding="utf-8")

        with open_child_log(log_file, ["pythonw.exe"], max_bytes=100):
            pass

        assert (tmp_path / "portrait_satellite.log.1").read_text(encoding="utf-8") == "x" * 200
        assert "x" * 200 not in log_file.read_text(encoding="utf-8")

    def test_creates_the_state_directory(self, tmp_path: Path):
        # A player can be launched before anything has created state/, and the
        # log has to exist before the child it is capturing starts.
        log_file = tmp_path / "state" / "portrait_satellite.log"

        with open_child_log(log_file, ["pythonw.exe"]):
            pass

        assert log_file.exists()
