"""A satellite must ask SDL for the click that focuses its window.

A satellite is never the focused window — the sequencer places every player with
SWP_NOACTIVATE — so the click that lands on a HUD button is also the click that
focuses the window, and SDL drops that one unless
``player_core.sdl_hints.deliver_the_focusing_click`` has run.  Without it every
press on the map has to be made twice.

Read off the source rather than exercised: ``satellite.app`` needs a real window
and the libmpv DLL, so nothing here can run it.  The same guard lives in genau
over Nau's and Genau's window paths, which is the other half of "every player in
this family behaves the same on the first click".
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "satellite" / "app.py"


def _call_lines() -> tuple[list[int], list[int]]:
    """(lines calling deliver_the_focusing_click, lines calling pygame.init)."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    hint: list[int] = []
    init: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name) and target.id == "deliver_the_focusing_click":
            hint.append(node.lineno)
        elif (isinstance(target, ast.Attribute) and target.attr == "init"
                and isinstance(target.value, ast.Name) and target.value.id == "pygame"):
            init.append(node.lineno)
    return hint, init


def test_the_focusing_click_is_asked_for_before_the_window_exists():
    """SDL reads the hint when the click arrives, but the window must not have
    been created first — so the call comes before ``pygame.init()``."""
    hint, init = _call_lines()

    assert hint, "satellite/app.py never calls deliver_the_focusing_click"
    assert init, "satellite/app.py no longer calls pygame.init — move this guard"
    assert max(hint) < min(init), (
        "satellite/app.py asks for the focusing click after its window exists"
    )
