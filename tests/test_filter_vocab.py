from __future__ import annotations

from fun_time.filter_vocab import (
    FILTER_ACTS,
    clear_command,
    decode_filter_command,
    filter_voice_commands,
    set_command,
    set_commands_for_scope,
    spoken_forms_for_both,
)


def test_set_and_clear_commands_round_trip_through_decode():
    for scope in ("both", "portrait", "landscape"):
        for query in FILTER_ACTS:
            assert decode_filter_command(set_command(scope, query)) == (scope, query)
        assert decode_filter_command(clear_command(scope)) == (scope, "")


def test_decode_returns_none_for_non_filter_commands():
    for command in ("fmode_toggle", "portrait_next", "recency_order_refresh", "filterish", ""):
        assert decode_filter_command(command) is None


def test_bare_act_filters_both_orientation_prefix_scopes_one():
    voice = filter_voice_commands()
    assert voice["delta"] == set_command("both", "delta")
    assert voice["portrait delta"] == set_command("portrait", "delta")
    assert voice["landscape delta"] == set_command("landscape", "delta")


def test_spoken_form_differs_from_the_matched_query():
    voice = filter_voice_commands()
    # "alpha" is out-of-lexicon: the phrase is "alpha form" but the command —
    # and therefore the metadata substring — is the real "alpha".
    assert "alpha" not in voice
    assert voice["alpha form"] == set_command("both", "alpha")
    assert decode_filter_command(voice["alpha form"]) == ("both", "alpha")


def test_beta_gamma_matches_the_users_example_phrase():
    voice = filter_voice_commands()
    assert decode_filter_command(voice["portrait beta gamma"]) == ("portrait", "beta gamma")


def test_the_acts_evolver_backfills_are_filterable():
    """Evolver's backfill tool dictates these acts; they must be reachable here."""
    voice = filter_voice_commands()
    assert decode_filter_command(voice["zeta"]) == ("both", "zeta")
    assert decode_filter_command(voice["delta"]) == ("both", "delta")
    assert decode_filter_command(voice["portrait other"]) == ("portrait", "other")


def test_clear_phrases_map_to_clear_commands():
    voice = filter_voice_commands()
    assert voice["clear filter"] == clear_command("both")
    assert voice["clear portrait"] == clear_command("portrait")
    assert voice["clear landscape"] == clear_command("landscape")


def test_every_generated_voice_command_decodes():
    for command in filter_voice_commands().values():
        assert decode_filter_command(command) is not None


def test_set_commands_for_scope_lists_every_act_without_the_clear():
    for scope in ("both", "portrait", "landscape"):
        commands = set_commands_for_scope(scope)
        assert set_command(scope, "beta gamma") in commands
        assert clear_command(scope) not in commands
        assert len(commands) == len(FILTER_ACTS)


def test_spoken_forms_for_both_covers_every_act_form():
    forms = spoken_forms_for_both()
    assert "alpha form" in forms
    assert "beta gamma" in forms
    assert len(forms) == sum(len(v) for v in FILTER_ACTS.values())
