from __future__ import annotations

from fun_time.event_log import SOURCE_LANDSCAPE, SOURCE_MAIN, SOURCE_PORTRAIT
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.satellite_groups import satellite_source


def test_funestras_keep_the_numbers_the_saved_state_stores():
    assert FunTimeFunestra.MAIN == 1
    assert FunTimeFunestra.PORTRAIT == 2
    assert FunTimeFunestra.LANDSCAPE == 3
    assert str(FunTimeFunestra.PORTRAIT) == "2"
    assert f"{FunTimeFunestra.LANDSCAPE}" == "3"
    assert int(FunTimeFunestra(int("2"))) == 2


def test_labels_are_the_lowercase_names():
    assert FunTimeFunestra.MAIN.label == "main"
    assert FunTimeFunestra.PORTRAIT.label == "portrait"
    assert FunTimeFunestra.LANDSCAPE.label == "landscape"


def test_the_satellites_are_the_portrait_then_the_landscape_funestra():
    assert FunTimeFunestra.SATELLITES == (FunTimeFunestra.PORTRAIT, FunTimeFunestra.LANDSCAPE)


def test_the_event_log_sources_are_exactly_the_funestra_labels():
    assert FunTimeFunestra.MAIN.label == SOURCE_MAIN
    assert FunTimeFunestra.PORTRAIT.label == SOURCE_PORTRAIT
    assert FunTimeFunestra.LANDSCAPE.label == SOURCE_LANDSCAPE


def test_a_satellites_label_is_the_source_its_log_lines_carry():
    assert satellite_source(FunTimeFunestra.PORTRAIT) == SOURCE_PORTRAIT
    assert satellite_source(FunTimeFunestra.LANDSCAPE) == SOURCE_LANDSCAPE
