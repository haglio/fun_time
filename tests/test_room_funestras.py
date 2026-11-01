"""The Funestra identity type — one vocabulary for what half the repo carried as
an int slot and the other half as a name string (audit finding
fun_time/A_dispatch/design/006)."""
from __future__ import annotations

from fun_time.event_log import SOURCE_LANDSCAPE, SOURCE_MAIN, SOURCE_PORTRAIT
from fun_time.room_funestras import RoomFunestra


def test_funestras_keep_the_slot_numbers_the_ini_stores():
    """active_funestra is persisted as str(state.active_funestra) and read back with
    int(); an IntEnum round-trips identically, so the wire value is pinned."""
    assert RoomFunestra.MAIN == 1
    assert RoomFunestra.PORTRAIT == 2
    assert RoomFunestra.LANDSCAPE == 3
    assert str(RoomFunestra.PORTRAIT) == "2"
    assert f"{RoomFunestra.LANDSCAPE}" == "3"
    assert int(RoomFunestra(int("2"))) == 2


def test_labels_are_the_names_the_drawing_side_uses():
    assert RoomFunestra.MAIN.label == "main"
    assert RoomFunestra.PORTRAIT.label == "portrait"
    assert RoomFunestra.LANDSCAPE.label == "landscape"


def test_label_of_tolerates_a_slot_no_funestra_holds():
    """A hand-edited state file can carry any int; the label crossing answers
    "" for it, as side_name always did, rather than raising mid-tick."""
    assert RoomFunestra.label_of(2) == "portrait"
    assert RoomFunestra.label_of(7) == ""


def test_the_satellites_scope_is_the_two_satellites():
    assert RoomFunestra.SATELLITES == (RoomFunestra.PORTRAIT, RoomFunestra.LANDSCAPE)
    assert RoomFunestra.for_scope("satellites") == RoomFunestra.SATELLITES
    assert RoomFunestra.for_scope("portrait") == (RoomFunestra.PORTRAIT,)
    assert RoomFunestra.for_scope("landscape") == (RoomFunestra.LANDSCAPE,)


def test_the_event_log_sources_are_exactly_the_funestra_labels():
    """satellite_source answers Funestra(which).label, which is only sound while
    the event_log constants and the labels are the same strings."""
    assert RoomFunestra.MAIN.label == SOURCE_MAIN
    assert RoomFunestra.PORTRAIT.label == SOURCE_PORTRAIT
    assert RoomFunestra.LANDSCAPE.label == SOURCE_LANDSCAPE
