from __future__ import annotations

import pytest

from fun_time.aiming import command_funestra, message_source, resolve_active_funestra_command
from fun_time.event_log import SOURCE_LANDSCAPE, SOURCE_MAIN, SOURCE_PORTRAIT, SOURCE_SYSTEM
from fun_time.fun_time_funestras import FunTimeFunestra


@pytest.mark.parametrize(("command", "funestra"), [
    ("portrait_next", FunTimeFunestra.PORTRAIT),
    ("landscape_next", FunTimeFunestra.LANDSCAPE),
    ("main_funestra_speed_up", FunTimeFunestra.MAIN),
    ("genau_pause", FunTimeFunestra.MAIN),
    ("omnipause_toggle", None),
    ("active_next", None),
])
def test_a_command_addresses_the_funestra_its_name_starts_with_or_none(command, funestra):
    assert command_funestra(command) is funestra


def test_a_command_that_is_not_active_is_left_as_it_was_said():
    assert resolve_active_funestra_command("portrait_next", FunTimeFunestra.LANDSCAPE) == "portrait_next"


def test_an_active_command_goes_to_the_satellite_that_is_active():
    assert resolve_active_funestra_command("active_next", FunTimeFunestra.PORTRAIT) == "portrait_next"
    assert resolve_active_funestra_command("active_next", FunTimeFunestra.LANDSCAPE) == "landscape_next"


def test_an_active_command_the_main_funestra_has_no_form_of_stays_active():
    assert resolve_active_funestra_command("active_no_such_thing", FunTimeFunestra.MAIN) == "active_no_such_thing"


@pytest.mark.parametrize(("command", "active", "source"), [
    ("portrait_next", FunTimeFunestra.MAIN, SOURCE_PORTRAIT),
    ("landscape_next", None, SOURCE_LANDSCAPE),
    ("main_funestra_speed_up", None, SOURCE_MAIN),
    ("active_next", FunTimeFunestra.LANDSCAPE, SOURCE_LANDSCAPE),
    ("active_next", None, SOURCE_SYSTEM),
    ("omnipause_toggle", FunTimeFunestra.PORTRAIT, SOURCE_SYSTEM),
])
def test_a_message_is_about_the_funestra_addressed_else_the_whole_session(command, active, source):
    assert message_source(command, active) == source
