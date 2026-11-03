from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FUNESTRA_LOOPS = {
    REPO / "main_funestra" / "window.py": "deal",
    REPO / "main_funestra" / "input.py": "close_requested",
}


def _calls(source: Path, name: str) -> bool:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
        for node in ast.walk(tree)
    )


def test_the_loop_routes_its_quit_through_the_session():
    missing = [
        source.relative_to(REPO).as_posix()
        for source, call in FUNESTRA_LOOPS.items()
        if not _calls(source, call)
    ]

    assert not missing, f"these end themselves instead of asking the session: {missing}"
