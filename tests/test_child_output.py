"""No child of a session inherits the launcher's console, ever.

This is the launch that takes two clicks, and the whole of it.

``launch.vbs`` runs the orchestrator through ``cmd /c ... >> launcher.log``, so
``cmd`` owns that file for as long as the session lasts and Windows lets nobody
else write it meanwhile.  A ``Popen`` that names no ``stdout``/``stderr`` does
not send the child's output nowhere — it hands the child *this process's* pair,
which is that same handle.  Most children die with the session and give it back.
Some do not: Chrome opened for the Random Favs Browser, the broker tray (whose
own docstring says it must outlive the run), an Origenerator kept across a
crossing to the headset.  One of those goes on holding ``launcher.log`` for as
long as its own window is open, which can be hours.

Then the next launch redirects into the held file, and ``cmd`` fails on the
redirect — before it runs python at all.  Nothing starts.  ``orchestrator.log``
gets no line, because logging is configured inside the interpreter that never
ran; the single-instance mutex says nothing, for the same reason; and the
launcher's own dialog, if it is seen at all, shows the tail of a log written by
a session that ended long ago.  The click does nothing, and the app starts on a
later click only because the stray has finally gone.

So every ``Popen`` in the tree says where the child's output goes.  Either the
child keeps a log of its own (``open_child_log``) or it keeps none
(``no_child_log``, which is ``DEVNULL``) — never the inherited default.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = (ROOT / "fun_time", ROOT / "satellite", ROOT / "fun_time_vr")

# The one helper that supplies the pair by unpacking.  A call may instead name
# stdout and stderr itself; anything else is the inherited default.
_SUPPLIES_THE_PAIR = "no_child_log"


def _popen_calls() -> list[tuple[Path, ast.Call]]:
    found = []
    for path in sorted(p for pkg in PACKAGES for p in pkg.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_popen(node.func):
                found.append((path, node))
    return found


def _is_popen(func: ast.expr) -> bool:
    return isinstance(func, ast.Attribute) and func.attr == "Popen"


def _says_where_the_output_goes(call: ast.Call) -> bool:
    named = {keyword.arg for keyword in call.keywords}
    if "stdout" in named and "stderr" in named:
        return True
    return any(
        keyword.arg is None
        and isinstance(keyword.value, ast.Call)
        and isinstance(keyword.value.func, ast.Name)
        and keyword.value.func.id == _SUPPLIES_THE_PAIR
        for keyword in call.keywords
    )


def test_there_are_popen_calls_to_check():
    """The sweep below passes vacuously if the scan stops finding them."""
    assert len(_popen_calls()) >= 15


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


def test_the_helper_sends_the_pair_to_devnull():
    import subprocess

    from fun_time.child_log import no_child_log

    assert no_child_log() == {
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
