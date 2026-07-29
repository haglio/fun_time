from __future__ import annotations

import json
from pathlib import Path

from fun_time.filter_vocab import (
    decode_filter_command,
    display_forms,
    filter_voice_commands,
    load_filter_acts,
    set_command,
    set_commands_for_scope,
)

# A tame stand-in for the real (git-ignored) vocabulary. It exercises every
# mechanic the module has: a plain act, a multi-word query, and an act whose
# spoken form differs from the query it matches.
ACTS = {
    "alpha": ("alpha",),
    "beta gamma": ("beta gamma",),
    "delta": ("delta form",),
}


def test_set_commands_round_trip_through_decode():
    for scope in ("both", "portrait", "landscape"):
        for query in ACTS:
            assert decode_filter_command(set_command(scope, query)) == (scope, query)


def test_decode_returns_none_for_non_filter_commands():
    for command in ("fmode_toggle", "portrait_next", "recency_order_refresh", "filterish", ""):
        assert decode_filter_command(command) is None


def test_bare_act_filters_both_orientation_prefix_scopes_one():
    voice = filter_voice_commands(ACTS)
    assert voice["alpha"] == set_command("both", "alpha")
    assert voice["portrait alpha"] == set_command("portrait", "alpha")
    assert voice["landscape alpha"] == set_command("landscape", "alpha")


def test_spoken_form_can_differ_from_the_matched_query():
    voice = filter_voice_commands(ACTS)
    # "delta" is voiced as "delta form", but the command — and therefore the
    # metadata substring — stays the real query "delta".
    assert "delta" not in voice
    assert voice["delta form"] == set_command("both", "delta")
    assert decode_filter_command(voice["delta form"]) == ("both", "delta")


def test_multi_word_query_round_trips():
    voice = filter_voice_commands(ACTS)
    assert decode_filter_command(voice["portrait beta gamma"]) == ("portrait", "beta gamma")


def test_clearing_a_filter_is_not_this_module_s_business():
    """It was — "clear filter" for both sides, "clear portrait" for one — and that
    put the side word where every other satellite phrase puts an action's own
    words.  Clearing is "no filter" in the grid now, so it scopes like the rest:
    "portrait clear filter", either order, bare for the side last navigated."""
    voice = filter_voice_commands(ACTS)
    for phrase in ("clear filter", "show everything", "clear portrait", "clear landscape"):
        assert phrase not in voice
    assert all("_clear" not in command for command in voice.values())


def test_every_generated_voice_command_decodes():
    for command in filter_voice_commands(ACTS).values():
        assert decode_filter_command(command) is not None


def test_set_commands_for_scope_lists_every_act():
    for scope in ("both", "portrait", "landscape"):
        commands = set_commands_for_scope(scope, ACTS)
        assert set_command(scope, "beta gamma") in commands
        assert len(commands) == len(ACTS)


def test_display_forms_read_as_the_act_not_as_the_recognizer_hears_it():
    """"delta" is voiced "delta form" because the model has no token for the real
    word.  The reference must show "delta": the workaround is a fact about the
    speech model, and printing it would teach the reader a word for the act that
    nobody uses for it."""
    forms = display_forms(ACTS)
    assert set(forms) == set(ACTS)
    assert "delta" in forms and "delta form" not in forms
    assert "beta gamma" in forms  # a multi-word query reads as itself


def test_the_default_vocabulary_is_loaded_and_usable():
    """Called with no acts, the API works off whatever overlay is present."""
    voice = filter_voice_commands()
    assert voice  # non-empty
    assert all(decode_filter_command(cmd) is not None for cmd in voice.values())


class TestLoadFilterActs:
    def test_prefers_local_over_example(self, tmp_path: Path):
        local = tmp_path / "content.local.json"
        example = tmp_path / "content.example.json"
        local.write_text(json.dumps({"filter_acts": {"x": ["x"]}}), encoding="utf-8")
        example.write_text(json.dumps({"filter_acts": {"y": ["y"]}}), encoding="utf-8")
        assert load_filter_acts(local, example) == {"x": ("x",)}

    def test_falls_back_to_example_when_local_absent(self, tmp_path: Path):
        example = tmp_path / "content.example.json"
        example.write_text(json.dumps({"filter_acts": {"y": ["y", "y two"]}}), encoding="utf-8")
        acts = load_filter_acts(tmp_path / "missing.json", example)
        assert acts == {"y": ("y", "y two")}
