"""The buttons Fun Time declares on a satellite's HUD: what each posts, its face,
its tooltip, its state, and the groups they fall into."""
from __future__ import annotations

from itertools import pairwise

from player_core.hud_button import FIT_THE_WORD, Button
from player_core.hud_marks import FMODE_ICON, MINIMIZE_ICON, SHARED_MARK, shared_mark_name
from player_core.modes import SatellitesMode
from shared_ui.icon_geometry import glyph_names

from fun_time.satellite_buttons import CONTROL_FACES, player_rows
from tests.symbol_face import typed_in_the_symbol_face


def _band(**fields) -> tuple[Button, ...]:
    return player_rows("portrait", **fields)[-1]


def _names(buttons: tuple[Button, ...]) -> list[str]:
    return [button.command.removeprefix("portrait_") for button in buttons]


def test_the_band_is_the_players_own_controls_in_the_consoles_order():
    """The browse pair, then the three about the clip on screen and the library it
    came from, then the reset, then the browse order, then another rendition of
    the clip, as the console ends its own browse controls on it, then minimize —
    ending with the one that acts on the window rather than on anything in it."""
    assert _names(_band(latest=False)) == [
        "prev", "next", "lock", "trash", "fmode", "reset", "shuffle", "latest",
        "cycle_version", "minimize", "crown"]


def test_every_button_posts_that_players_own_verb():
    """"portrait_prev", "landscape_trash": the dispatch loop needs no new verbs
    for a button, only for the thing it does."""
    for player in ("portrait", "landscape"):
        for button in player_rows(player, latest=True)[-1]:
            assert button.command.startswith(f"{player}_"), button.command
            assert button.tooltip, button.command


def test_the_band_breaks_into_the_groups_the_console_breaks_into():
    """A run of evenly spaced squares reads as one long undifferentiated strip;
    the wider gap opens where the controls stop being about the same thing, at
    the same seams the console's rows break at."""
    starts = [button.command.removeprefix("portrait_")
              for button in _band(latest=False) if button.group_break]

    assert starts == ["lock", "reset", "shuffle", "cycle_version", "minimize"]


def test_the_states_light_and_nothing_else_does():
    """The lock and F-mode are states the side sits in, in the favorites' green;
    exactly one of the order pair is lit, saying which order the browse is in;
    a step, the bin, reset and minimize are things done, never lit."""
    band = _band(locked=True, favorites_filter=True, latest=True)
    lit = dict(zip(_names(band), band))

    assert lit["lock"].lit and lit["lock"].favorite
    assert lit["fmode"].lit and lit["fmode"].favorite
    assert lit["latest"].lit and not lit["shuffle"].lit
    assert not any(lit[name].lit for name in (
        "prev", "next", "trash", "reset", "cycle_version", "minimize"))
    shuffled = dict(zip(_names(_band(latest=False)), _band(latest=False)))
    assert shuffled["shuffle"].lit and not shuffled["latest"].lit


def test_the_order_pair_is_declared_only_where_the_order_can_be_switched():
    """The origenerator-mode panel has an order but no way to change it from
    here, and two buttons nothing answers are two dead buttons."""
    assert "shuffle" not in _names(_band()) and "latest" not in _names(_band())
    assert "shuffle" in _names(_band(latest=False))


def test_the_bin_takes_something_away():
    trash = next(b for b in _band() if b.command == "portrait_trash")

    assert trash.danger


def test_the_mode_pair_leads_where_the_session_hosts_an_origenerator():
    """A row of its own above the band, like the console's Video/Genau row: the
    side's current mode lit, minimize riding the row a group apart, and no such
    row at all for a session hosting no Origenerator."""
    rows = player_rows("portrait", satellites_mode=SatellitesMode.ORIGENERATOR)

    assert len(rows) == 2
    assert [b.command for b in rows[0]] == [
        "satellites_video_activate", "origenerator_activate", "portrait_minimize",
        "portrait_crown"]
    assert [b.lit for b in rows[0]][:3] == [False, True, False]
    assert [b.width for b in rows[0][:2]] == [FIT_THE_WORD, FIT_THE_WORD]
    assert rows[0][2].group_break and rows[0][2].glyph == MINIMIZE_ICON
    assert "minimize" not in _names(rows[1])
    assert len(player_rows("portrait")) == 1


def test_a_player_in_the_headset_has_no_window_to_minimize():
    """Minimize parks a player's window, and in the headset a player is a
    screen in the scene rather than a window of its own: there is nothing for
    it to park, so it is left off the band and off the session's row alike."""
    plain = player_rows("portrait", latest=False, in_vr=True)
    hosting = player_rows("portrait", latest=False, satellites_mode=SatellitesMode.VIDEO,
                          in_vr=True)

    assert _names(plain[-1])[-1] == "cycle_version"
    assert [b.command for b in hosting[0]] == [
        "satellites_video_activate", "origenerator_activate"]
    assert "minimize" not in _names(hosting[-1])


def test_the_origenerator_button_is_dim_until_that_app_is_up():
    """The room opens without waiting out the hosted app's boot, so for the
    first half-minute the switch would land on windows that do not exist.  Dim
    is this family's unpressable state: the player draws it faded, posts
    nothing for a press on it, and still answers a hover -- with what it is
    waiting for, since knowing why it cannot be pressed is the point."""
    starting = {b.command: b for b in player_rows(
        "portrait", satellites_mode=SatellitesMode.VIDEO, origenerator_ready=False)[0]}
    ready = {b.command: b for b in player_rows(
        "portrait", satellites_mode=SatellitesMode.VIDEO, origenerator_ready=True)[0]}

    assert starting["origenerator_activate"].dim
    assert "starting" in starting["origenerator_activate"].tooltip
    assert not starting["satellites_video_activate"].dim
    assert not ready["origenerator_activate"].dim
    assert "starting" not in ready["origenerator_activate"].tooltip


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
            previous.command.removeprefix("portrait_")
            in ("next", "fmode", "reset", "latest", "cycle_version"))


def test_the_typed_faces_are_in_the_painters_symbol_face():
    typed = {face for face in CONTROL_FACES.values() if len(face) == 1 and not face.isalnum()}

    assert typed
    for face in typed:
        assert typed_in_the_symbol_face(face), ascii(face)


def test_the_versions_button_is_dim_where_the_clip_has_only_itself():
    """The console's own versions mark, and dim where there is nothing to step
    to — the way the main player's is, with the hover saying so."""
    alone = {b.command: b for b in _band()}["portrait_cycle_version"]
    paired = {b.command: b for b in _band(has_other_versions=True)}["portrait_cycle_version"]

    assert alone.dim and "none for this one" in alone.tooltip
    assert not paired.dim and "none" not in paired.tooltip
    assert shared_mark_name(CONTROL_FACES["cycle_version"]) == "versions"


def test_the_portrait_players_crown_is_lit_while_it_holds_the_crown():
    held = _band(crowned=True)[-1]
    given_away = _band(crowned=False)[-1]

    assert (held.command, held.lit, given_away.lit) == ("portrait_crown", True, False)
    assert held.tooltip.startswith("Crowned")


def test_only_the_portrait_player_wears_a_crown():
    assert "landscape_crown" not in [
        button.command for row in player_rows("landscape", latest=False,
                                               satellites_mode=SatellitesMode.VIDEO)
        for button in row]
