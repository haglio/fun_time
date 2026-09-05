"""Nothing ends a satellite on its own — not a key, and not the window's close.

A satellite is one of a set the sequencer placed, so ending one alone leaves the
session running around a hole nothing refills.  Two roads lead there and both are
shut: the loop answers no keyboard event at all (``satellite/app.py`` used to
have a Ctrl+Q handler and its comment says not to put it back), and the close
every Windows window has — Alt+F4, the taskbar, the system menu — is asked of the
session instead of answered here.

The close is the one that bit.  Opt+Cmd+Q on a Mac keyboard arrives as Alt+F4, so
it took out Nau, then the portrait satellite, then the landscape one, a press at
a time, while the dashboard, Genau and the audio companion carried on and the
session had to be ended by voice.  The gesture itself is
``player_core.session_quit``'s, and tested there; what is here is that this
loop routes the close to it, and that the verb it posts is the dashboard's.

The scans read ``satellite/app.py`` off the source rather than running it: it
needs a real window and the libmpv DLL, the same reason
``test_satellite_focus_clickthrough`` reads its guarantee that way.
"""
from __future__ import annotations

import ast
from pathlib import Path

from player_core.session_quit import SESSION_QUIT

SOURCE = Path(__file__).resolve().parents[1] / "satellite" / "app.py"

# The event types this loop is allowed to answer.  QUIT is here because the loop
# must *see* the close in order to hand it to the session — what it may not do is
# stop on it.
ALLOWED_EVENTS = {"QUIT", "MOUSEBUTTONDOWN", "MOUSEMOTION"}


def test_the_ask_is_the_dashboards_own_quit_verb():
    """What the Quit button posts and the dispatch loop turns into "exit" for
    the bridge.  Rename it and a closed satellite asks for something nothing
    answers, so the gesture goes quiet rather than wrong."""
    from fun_time.dashboard_actions import QUIT_BUTTON

    assert SESSION_QUIT == QUIT_BUTTON


def _tree() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def _pygame_event_names() -> set[str]:
    """Every ``pygame.<NAME>`` the module tests an event's ``.type`` against."""
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


def test_the_loop_answers_no_keyboard_event():
    """KEYDOWN or KEYUP here is the handler that was taken out, coming back."""
    assert not _pygame_event_names() & {"KEYDOWN", "KEYUP", "TEXTINPUT"}, (
        "satellite/app.py answers a key again — a key that ends one satellite "
        "leaves the session running around a hole nothing refills"
    )


def test_nothing_else_has_crept_into_the_loop_either():
    """Held to the events it is known to answer, so a new one is looked at rather
    than assumed harmless — the Ctrl+Q handler this guards against was itself
    once an obvious convenience."""
    assert _pygame_event_names() <= ALLOWED_EVENTS, (
        f"satellite/app.py answers {sorted(_pygame_event_names() - ALLOWED_EVENTS)}; "
        "if that is right, say why here and add it to ALLOWED_EVENTS"
    )


def test_the_close_is_routed_to_the_session_rather_than_answered():
    """The loop must not reach its own stop event straight from a QUIT.  That is
    what closed one player at a time, and it is invisible standalone, where
    stopping is exactly right."""
    routed = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "quit_gesture"
        for node in ast.walk(_tree())
    )

    assert routed, "satellite/app.py ends itself on a close instead of asking the session"
