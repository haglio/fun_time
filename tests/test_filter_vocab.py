from __future__ import annotations

import json
from pathlib import Path

from fun_time.filter_vocab import (
    clear_command,
    decode_filter_command,
    filter_voice_commands,
    load_filter_acts,
    set_command,
    set_commands_for_scope,
    spoken_forms_for_both,
)

# A tame stand-in for the real (git-ignored) vocabulary. It exercises every
# mechanic the module has: a plain act, a multi-word query, and an act whose
# spoken form differs from the query it matches.
ACTS = {
    "alpha": ("alpha",),
    "beta gamma": ("beta gamma",),
    "delta": ("delta form",),
}


def test_set_and_clear_commands_round_trip_through_decode():
    for scope in ("both", "portrait", "landscape"):
        for query in ACTS:
            assert decode_filter_command(set_command(scope, query)) == (scope, query)
        assert decode_filter_command(clear_command(scope)) == (scope, "")


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


def test_clear_phrases_map_to_clear_commands():
    voice = filter_voice_commands(ACTS)
    assert voice["clear filter"] == clear_command("both")
    assert voice["clear portrait"] == clear_command("portrait")
    assert voice["clear landscape"] == clear_command("landscape")


def test_every_generated_voice_command_decodes():
    for command in filter_voice_commands(ACTS).values():
        assert decode_filter_command(command) is not None


def test_set_commands_for_scope_lists_every_act_without_the_clear():
    for scope in ("both", "portrait", "landscape"):
        commands = set_commands_for_scope(scope, ACTS)
        assert set_command(scope, "beta gamma") in commands
        assert clear_command(scope) not in commands
        assert len(commands) == len(ACTS)


def test_spoken_forms_for_both_covers_every_act_form():
    forms = spoken_forms_for_both(ACTS)
    assert "delta form" in forms
    assert "beta gamma" in forms
    assert len(forms) == sum(len(v) for v in ACTS.values())


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
