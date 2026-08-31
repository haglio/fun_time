"""The Player identity type — one vocabulary for what half the repo carried as
an int slot and the other half as a name string (audit finding
fun_time/A_dispatch/design/006)."""
from __future__ import annotations

from fun_time.players import Player


def test_players_keep_the_slot_numbers_the_ini_stores():
    """active_side is persisted as str(state.active_side) and read back with
    int(); an IntEnum round-trips identically, so the wire value is pinned."""
    assert Player.MAIN == 1
    assert Player.PORTRAIT == 2
    assert Player.LANDSCAPE == 3
    assert str(Player.PORTRAIT) == "2"
    assert f"{Player.LANDSCAPE}" == "3"
    assert int(Player(int("2"))) == 2


def test_labels_are_the_names_the_drawing_side_uses():
    assert Player.MAIN.label == "main"
    assert Player.PORTRAIT.label == "portrait"
    assert Player.LANDSCAPE.label == "landscape"


def test_label_of_tolerates_a_slot_no_player_holds():
    """A hand-edited state file can carry any int; the label crossing answers
    "" for it, as side_name always did, rather than raising mid-tick."""
    assert Player.label_of(2) == "portrait"
    assert Player.label_of(7) == ""


def test_satellites_are_the_scope_both_means():
    assert Player.SATELLITES == (Player.PORTRAIT, Player.LANDSCAPE)
    assert Player.for_scope("both") == Player.SATELLITES
    assert Player.for_scope("portrait") == (Player.PORTRAIT,)
    assert Player.for_scope("landscape") == (Player.LANDSCAPE,)


def test_the_event_log_sources_are_exactly_the_player_labels():
    """satellite_source answers Player(which).label, which is only sound while
    the event_log constants and the labels are the same strings."""
    from fun_time.event_log import SOURCE_LANDSCAPE, SOURCE_MAIN, SOURCE_PORTRAIT

    assert SOURCE_MAIN == Player.MAIN.label
    assert SOURCE_PORTRAIT == Player.PORTRAIT.label
    assert SOURCE_LANDSCAPE == Player.LANDSCAPE.label
