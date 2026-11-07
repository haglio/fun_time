from __future__ import annotations

from fun_time.command_value import FILTER, parse_command
from fun_time.filter_vocab import (
    display_forms,
    filter_voice_commands,
    set_command,
    set_commands_for_scope,
)

ACTS = {
    "alpha": ("alpha",),
    "beta gamma": ("beta gamma",),
    "delta": ("delta form",),
}


def _decoded(command: str) -> tuple[str, str] | None:
    said = parse_command(command)
    return (said.said_to, said.argument) if said.action == FILTER else None


def test_set_commands_round_trip_through_the_command_value():
    for scope in ("satellites", "portrait", "landscape"):
        for query in ACTS:
            assert _decoded(set_command(scope, query)) == (scope, query)


def test_the_wire_format_is_the_filter_prefix_scope_and_slugged_query():
    assert set_command("satellites", "beta gamma") == "filter_satellites_beta_gamma"
    assert set_command("portrait", "alpha") == "filter_portrait_alpha"
    assert set_command("landscape", "delta") == "filter_landscape_delta"
    assert _decoded("filter_satellites_beta_gamma") == ("satellites", "beta gamma")


def test_a_command_that_is_not_about_the_filter_is_left_alone():
    for command in ("fmode_toggle", "portrait_next", "recency_order_refresh", "filterish", ""):
        assert _decoded(command) is None


def test_bare_act_filters_both_orientation_prefix_scopes_one():
    voice = filter_voice_commands(ACTS)
    assert voice["alpha"] == set_command("satellites", "alpha")
    assert voice["portrait alpha"] == set_command("portrait", "alpha")
    assert voice["landscape alpha"] == set_command("landscape", "alpha")


def test_satellites_names_the_pair_before_or_after_the_act():
    voice = filter_voice_commands(ACTS)
    assert voice["satellites alpha"] == voice["alpha satellites"] == set_command("satellites", "alpha")


def test_spoken_form_can_differ_from_the_matched_query():
    voice = filter_voice_commands(ACTS)
    assert "delta" not in voice
    assert voice["delta form"] == set_command("satellites", "delta")
    assert _decoded(voice["delta form"]) == ("satellites", "delta")


def test_multi_word_query_round_trips():
    voice = filter_voice_commands(ACTS)
    assert _decoded(voice["portrait beta gamma"]) == ("portrait", "beta gamma")


def test_an_act_nobody_listed_still_decodes_to_its_scope_and_query():
    assert _decoded("filter_portrait_never_listed_act") == ("portrait", "never listed act")


def test_clearing_a_filter_is_not_this_module_s_business():
    voice = filter_voice_commands(ACTS)
    for phrase in ("clear filter", "show everything", "clear portrait", "clear landscape"):
        assert phrase not in voice
    assert all("_clear" not in command for command in voice.values())


def test_every_generated_voice_command_decodes():
    for command in filter_voice_commands(ACTS).values():
        assert _decoded(command) is not None


def test_set_commands_for_scope_lists_every_act():
    for scope in ("satellites", "portrait", "landscape"):
        commands = set_commands_for_scope(scope, ACTS)
        assert set_command(scope, "beta gamma") in commands
        assert len(commands) == len(ACTS)


def test_display_forms_read_as_the_act_not_as_the_recognizer_hears_it():
    forms = display_forms(ACTS)
    assert set(forms) == set(ACTS)
    assert "delta" in forms
    assert "delta form" not in forms
    assert "beta gamma" in forms
