"""No key ends a satellite on its own.

A satellite is one of a set the sequencer placed, so killing one alone leaves the
session running around a hole nothing refills.  The session ends as a whole or
not at all: Ctrl+Alt+Q, which the bridge turns into the teardown that takes these
processes down with it.  ``satellite/app.py`` used to have a Ctrl+Q handler that
quit whichever satellite had focus, and its comment says not to put it back —
this is that comment with teeth, because a comment cannot fail a suite.

The same failure survived in genau until it bit: the main player's Q check asked
only whether Ctrl was down, so the session's Ctrl+Alt+Q read as its own Ctrl+Q
and closed one window while the rest of Fun Time carried on.  ``quits_this_player``
is that repo's answer; declining every key is this one's.

Read off the source rather than exercised: ``satellite.app`` needs a real window
and the libmpv DLL, the same reason ``test_satellite_focus_clickthrough`` reads
its guarantee off the source.
"""
from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "satellite" / "app.py"

# The event types this loop is allowed to answer.  QUIT is the window's close —
# the gesture Windows itself offers, which is not a key and not this player
# choosing to go.
ALLOWED_EVENTS = {"QUIT", "MOUSEBUTTONDOWN", "MOUSEMOTION"}


def _pygame_event_names() -> set[str]:
    """Every ``pygame.<NAME>`` the module tests an event's ``.type`` against."""
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
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
    handled = _pygame_event_names()

    assert not handled & {"KEYDOWN", "KEYUP", "TEXTINPUT"}, (
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
