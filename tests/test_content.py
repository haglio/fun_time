"""Tests for fun_time.content overlay loading.

Expectations are derived from the committed ``content.example.json`` so they
hold on a fresh or public checkout (which has no ``content.local.json`` at all).
The stand-in overlays written here use fabricated vocabulary — never values
lifted from the real library.
"""
from __future__ import annotations

import json
from pathlib import Path

from fun_time.content import EXAMPLE_CONTENT, load_content, load_web_providers


def _example() -> dict:
    return json.loads(EXAMPLE_CONTENT.read_text(encoding="utf-8"))


class TestLoadContent:
    def test_absent_local_returns_the_example_wholesale(self, tmp_path: Path):
        result = load_content(tmp_path / "missing.json", EXAMPLE_CONTENT)
        assert result == _example()

    def test_missing_clip_jump_phrases_is_backfilled_from_the_example(self, tmp_path: Path):
        # The reported crash: a real overlay omitting this phrase list made
        # voice_commands' ``load_content()["clip_jump_phrases"]`` raise KeyError
        # at import.  A phrase list sensibly falls back to the example.
        local = tmp_path / "content.local.json"
        local.write_text(json.dumps({"filter_acts": {"zeta": ["zeta"]}}), encoding="utf-8")
        result = load_content(local, EXAMPLE_CONTENT)
        assert result["clip_jump_phrases"] == _example()["clip_jump_phrases"]

    def test_missing_filter_acts_is_backfilled_from_the_example(self, tmp_path: Path):
        # filter_vocab reads ``data["filter_acts"]`` directly at import too.
        local = tmp_path / "content.local.json"
        local.write_text(json.dumps({"clip_jump_phrases": ["skip ahead"]}), encoding="utf-8")
        result = load_content(local, EXAMPLE_CONTENT)
        assert result["filter_acts"] == _example()["filter_acts"]

    def test_a_present_key_is_not_overwritten_by_the_example(self, tmp_path: Path):
        local = tmp_path / "content.local.json"
        local.write_text(
            json.dumps({"clip_jump_phrases": ["skip ahead", "rewind that"]}),
            encoding="utf-8",
        )
        result = load_content(local, EXAMPLE_CONTENT)
        assert result["clip_jump_phrases"] == ["skip ahead", "rewind that"]

    def test_missing_web_providers_defaults_to_empty_not_the_example(self, tmp_path: Path):
        # web_providers holds placeholder gallery URLs in the example; a real
        # overlay that omits it must default to none, never to the example's
        # stand-in URLs — those would be written verbatim into favourites.
        assert _example()["web_providers"], "example is expected to define providers"
        local = tmp_path / "content.local.json"
        local.write_text(json.dumps({"filter_acts": {"zeta": ["zeta"]}}), encoding="utf-8")
        result = load_content(local, EXAMPLE_CONTENT)
        assert result["web_providers"] == []


class TestLoadWebProviders:
    def test_absent_local_uses_the_example_providers(self, tmp_path: Path):
        providers = load_web_providers(tmp_path / "missing.json", EXAMPLE_CONTENT)
        assert len(providers) == len(_example()["web_providers"])

    def test_overlay_omitting_web_providers_yields_none(self, tmp_path: Path):
        local = tmp_path / "content.local.json"
        local.write_text(json.dumps({"filter_acts": {"zeta": ["zeta"]}}), encoding="utf-8")
        assert load_web_providers(local, EXAMPLE_CONTENT) == ()
