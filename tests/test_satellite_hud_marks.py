"""The satellite HUD wears the family's marks, not lookalikes of them.

The HUD is painted into the video frame with Pillow and there is no Qt in a
satellite process, so every mark here used to be whatever Segoe UI Symbol
carried -- a bin that had nothing to do with the bin on Origenerator's toolbar,
a loop that was one arc where the family's is a circuit, a reset that was a
counterclockwise arrow and so read as an undo.  They come out of shared_ui's
geometry now, through player_core's HUD chrome, and these hold them to it.
"""
from __future__ import annotations

from player_core.hud_marks import SHARED_MARK, shared_mark_name

from satellite.hud_paint import _CONTROL_GLYPHS, _FAVORITE_GLYPH, _LOOP_GLYPH
from shared_ui.icon_geometry import glyph_names


def _named() -> dict[str, str]:
    """Every HUD face that names a shared mark, by the mark it names."""
    faces = dict(_CONTROL_GLYPHS) | {"loop": _LOOP_GLYPH, "favorite": _FAVORITE_GLYPH}
    return {
        key: shared_mark_name(face)
        for key, face in faces.items()
        if face.startswith(SHARED_MARK)
    }


def test_the_bin_is_the_bin_the_rest_of_the_family_wears():
    # The one the user could see was wrong: Fun Time's HUD bin and
    # Origenerator's toolbar bin were two unrelated drawings on one screen.
    assert _named()["trash"] == "trash"


def test_reset_is_the_gear_with_a_circular_arrow_at_its_corner():
    # It was a bare counterclockwise arrow, which is what an undo looks like
    # everywhere else here -- the gear is what says the act is about settings.
    assert _named()["reset"] == "reset"


def test_the_loop_buttons_wear_the_circuit_rather_than_a_single_arc():
    # A single arc says "back one step", which is undo's job. The family's loop
    # is two arrows chasing each other around a rounded rectangle.
    assert _named()["loop"] == "loop"


def test_the_favorite_mark_is_the_familys_star():
    # The same star Origenerator paints on a starred tile, so a bookmark learned
    # in one app reads in the other.
    assert _named()["favorite"] == "star"


def test_the_marks_the_hud_names_all_exist():
    # A typo would be a KeyError raised inside a running video overlay rather
    # than here, so the names are checked against the registry.
    missing = {key: name for key, name in _named().items() if name not in glyph_names()}
    assert not missing, f"HUD faces naming marks shared_ui does not have: {missing}"


def test_the_transport_and_the_padlock_stay_typed():
    # Not everything moves: the family draws no skip-track and no padlock, and
    # the symbol face carries both cleanly. A name here that shared_ui cannot
    # draw would be worse than the character it replaced.
    for control in ("prev", "next", "lock"):
        assert not _CONTROL_GLYPHS[control].startswith(SHARED_MARK)
