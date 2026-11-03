from __future__ import annotations

import ast
from pathlib import Path

from funestra.session_quit import SESSION_QUIT

from fun_time.dashboard_actions import QUIT_BUTTON

ROOT = Path(__file__).resolve().parents[1]
SATELLITE = ROOT / "satellite" / "app.py"
WINDOW_INPUT = ROOT / "main_funestra" / "input.py"

ALLOWED_EVENTS = {"QUIT", "MOUSEBUTTONDOWN", "MOUSEBUTTONUP", "MOUSEMOTION", "WINDOWLEAVE"}


def test_the_ask_is_the_dashboards_own_quit_verb():
    assert SESSION_QUIT == QUIT_BUTTON


def _tree(source: Path = WINDOW_INPUT) -> ast.Module:
    return ast.parse(source.read_text(encoding="utf-8"))


def _pygame_event_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Attribute) and left.attr == "type"):
            continue
        for other in node.comparators:
            if (isinstance(other, ast.Attribute)
                    and isinstance(other.value, ast.Name)
                    and other.value.id == "pygame"):
                names.add(other.attr)
    return names


def test_a_satellite_reads_its_window_through_that_loop():
    reads_it = any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "Input"
        for node in ast.walk(_tree(SATELLITE))
    )

    assert reads_it, "satellite/app.py reads its window's events some other way"


def test_the_loop_answers_no_keyboard_event():
    assert not _pygame_event_names() & {"KEYDOWN", "KEYUP", "TEXTINPUT"}, (
        "main_funestra/input.py answers a key again — a key that ends one satellite "
        "leaves the session running around a gap nothing refills"
    )


def test_nothing_else_has_crept_into_the_loop_either():
    assert _pygame_event_names() <= ALLOWED_EVENTS, (
        f"main_funestra/input.py answers {sorted(_pygame_event_names() - ALLOWED_EVENTS)}; "
        "if that is right, say why here and add it to ALLOWED_EVENTS"
    )


def test_the_close_is_handed_to_the_funestra_rather_than_answered():
    handed_on = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "close_requested"
        for node in ast.walk(_tree())
    )

    assert handed_on, "main_funestra/input.py ends itself on a close instead of asking the session"
