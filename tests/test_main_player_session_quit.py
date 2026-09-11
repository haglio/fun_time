"""The main player does not quit itself — it asks the session to quit.

Every gesture that ends a window on its own — the close button, Alt+F4, Ctrl+Q —
ends only that window, and inside a Fun Time session that is wrong: the
sequencer put six windows up together and there is nothing to refill the gap
one leaving makes.  It bit for real: Opt+Cmd+Q on a Mac keyboard arrives as
Alt+F4, so it closed the main player, then the portrait satellite, then the
landscape one, one press at a time, while the dashboard, Genau and the audio
companion carried on and the session had to be ended by voice.

Read off the source rather than run: the run loop needs a real window and the
libmpv DLL, the same reason ``test_focus_clickthrough`` reads its guarantee
that way.  The chain is two links — the loop hands its events to
``main_player.input``, and that module answers a QUIT by asking the session —
and both are scanned, because scanning only the second would pass a loop that
took its events back and ended itself, leaving ``input.py`` correct and unused.
A synthetic QUIT is fed to the module directly in ``test_main_player_input``.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Every link of the chain, and the call it must make.
PLAYER_LOOPS = {
    REPO / "main_player" / "app.py": "deal",
    REPO / "main_player" / "input.py": "take_quit_gesture",
}


def _calls(source: Path, name: str) -> bool:
    """Whether *source* calls *name*, plainly or through something holding it."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return any(
        isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
        for node in ast.walk(tree)
    )


def test_the_loop_routes_its_quit_through_the_session():
    """A loop that sets its own stop event straight from a QUIT event is the
    regression: it looks right standalone and takes one window out of a session."""
    missing = [
        source.relative_to(REPO).as_posix()
        for source, call in PLAYER_LOOPS.items()
        if not _calls(source, call)
    ]

    assert not missing, f"these end themselves instead of asking the session: {missing}"
