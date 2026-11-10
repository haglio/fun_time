from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
OPENERS_NEEDING_A_REAL_WINDOW_AND_LIBMPV = (REPO / "satellite" / "app.py", REPO / "main_funestra" / "app.py")


def _call_lines_read_off_the_source(source: Path) -> tuple[list[int], list[int]]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    hint: list[int] = []
    init: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name) and target.id == "before_the_window_exists":
            hint.append(node.lineno)
        elif (isinstance(target, ast.Attribute) and target.attr == "init"
                and isinstance(target.value, ast.Name) and target.value.id == "pygame"):
            init.append(node.lineno)
    return hint, init


@pytest.mark.parametrize("source", OPENERS_NEEDING_A_REAL_WINDOW_AND_LIBMPV, ids=lambda p: p.parent.name)
def test_the_focusing_click_is_asked_for_before_the_window_exists(source: Path):
    hint, init = _call_lines_read_off_the_source(source)

    assert hint, f"{source.parent.name}/app.py never calls before_the_window_exists"
    assert init, f"{source.parent.name}/app.py no longer calls pygame.init — move this guard"
    assert max(hint) < min(init), (
        f"{source.parent.name}/app.py asks for the focusing click after its window exists"
    )
