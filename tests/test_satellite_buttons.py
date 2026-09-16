"""The buttons Fun Time declares on a satellite's HUD: what each posts, its face,
its tooltip, its state, and the groups they fall into."""
from __future__ import annotations

from itertools import pairwise

from player_core.hud_button import FIT_THE_WORD, Button
from player_core.hud_marks import FMODE_ICON, MINIMIZE_ICON, SHARED_MARK, shared_mark_name
from shared_ui.icon_geometry import glyph_names

from fun_time.satellite_buttons import CONTROL_FACES, side_rows
from tests.symbol_face import typed_in_the_symbol_face


def _band(**fields) -> tuple[Button, ...]:
    return side_rows("portrait", **fields)[-1]


def _names(buttons: tuple[Button, ...]) -> list[str]:
    return [button.action.removeprefix("portrait_") for button in buttons]


def test_the_band_is_the_sides_own_controls_in_the_consoles_order():
    """The browse pair, then the three about the clip on screen and the library it
    came from, then the reset, then the browse order, then minimize — widening
    from the clip on screen out to the whole side and ending with the one that
    acts on the window rather than on anything in it."""
    assert _names(_band(latest=False)) == [
        "prev", "next", "lock", "trash", "fmode", "reset", "shuffle", "latest", "minimize"]


def test_every_button_posts_that_sides_own_verb():
    """"portrait_prev", "landscape_trash": the dispatch loop needs no new verbs
    for a button, only for the thing it does."""
    for side in ("portrait", "landscape"):
        for button in side_rows(side, latest=True)[-1]:
            assert button.action.startswith(f"{side}_"), button.action
            assert button.tooltip, button.action


def test_the_band_breaks_into_the_groups_the_console_breaks_into():
    """A run of evenly spaced squares reads as one long undifferentiated strip;
    the wider gap opens where the controls stop being about the same thing, at
    the same seams the console's rows break at."""
    starts = [button.action.removeprefix("portrait_")
              for button in _band(latest=False) if button.group_break]

    assert starts == ["lock", "reset", "shuffle", "minimize"]


def test_the_states_light_and_nothing_else_does():
    """The lock and F-mode are states the side sits in, in the favorites' green;
    exactly one of the order pair is lit, saying which order the browse is in;
    a step, the bin, reset and minimize are things done, never lit."""
    band = _band(locked=True, f_mode=True, latest=True)
    lit = dict(zip(_names(band), band))

    assert lit["lock"].lit and lit["lock"].favorite
    assert lit["fmode"].lit and lit["fmode"].favorite
    assert lit["latest"].lit and not lit["shuffle"].lit
    assert not any(lit[name].lit for name in ("prev", "next", "trash", "reset", "minimize"))
    shuffled = dict(zip(_names(_band(latest=False)), _band(latest=False)))
    assert shuffled["shuffle"].lit and not shuffled["latest"].lit


def test_the_order_pair_is_declared_only_where_the_order_can_be_switched():
    """The origenerator-mode panel has an order but no way to change it from
    here, and two buttons nothing answers are two dead buttons."""
    assert "shuffle" not in _names(_band()) and "latest" not in _names(_band())
    assert "shuffle" in _names(_band(latest=False))


def test_the_bin_takes_something_away():
    trash = next(b for b in _band() if b.action == "portrait_trash")

    assert trash.danger


def test_the_mode_pair_leads_where_the_session_hosts_an_origenerator():
    """A row of its own above the band, like the console's Video/Genau row: the
    side's current mode lit, minimize riding the row a group apart, and no such
    row at all for a session hosting no Origenerator."""
    rows = side_rows("portrait", mode="origenerator")

    assert len(rows) == 2
    assert [b.action for b in rows[0]] == [
        "satellites_video_activate", "origenerator_activate", "portrait_minimize"]
    assert [b.lit for b in rows[0]] == [False, True, False]
    assert [b.width for b in rows[0][:2]] == [FIT_THE_WORD, FIT_THE_WORD]
    assert rows[0][2].group_break and rows[0][2].glyph == MINIMIZE_ICON
    assert "minimize" not in _names(rows[1])
    assert len(side_rows("portrait")) == 1


def test_the_faces_are_the_familys_marks_where_it_has_them():
    """The bin is the bin Origenerator's toolbar wears, reset the gear with the
    circular arrow, F-mode its magenta badge; the transport and the padlock stay
    typed, since the family draws neither."""
    named = {name: shared_mark_name(face) for name, face in CONTROL_FACES.items()
             if face.startswith(SHARED_MARK)}

    assert named["trash"] == "trash" and named["reset"] == "reset"
    assert not set(named.values()) - set(glyph_names())
    assert CONTROL_FACES["fmode"] == FMODE_ICON
    assert all(not CONTROL_FACES[name].startswith(SHARED_MARK) for name in ("prev", "next", "lock"))


def test_the_gaps_fall_between_groups_and_nowhere_else():
    for previous, button in pairwise(_band(latest=False)):
        assert button.group_break == (
            previous.action.removeprefix("portrait_") in ("next", "fmode", "reset", "latest"))


def test_the_typed_faces_are_in_the_painters_symbol_face():
    typed = {face for face in CONTROL_FACES.values() if len(face) == 1 and not face.isalnum()}

    assert typed
    for face in typed:
        assert typed_in_the_symbol_face(face), ascii(face)
