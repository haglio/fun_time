from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from app_support import haglio_settings

from fun_time.content import (
    EXAMPLE_CONTENT,
    Noun,
    genau_flick_noun,
    load_content,
    load_web_providers,
)


def _example() -> dict:
    return json.loads(EXAMPLE_CONTENT.read_text(encoding="utf-8"))


class TestLoadContent:
    def test_without_a_local_overlay_the_committed_example_is_what_loads(self, tmp_path: Path):
        result = load_content(tmp_path / "missing.json", EXAMPLE_CONTENT)
        assert result == _example()

    def test_missing_video_jump_phrases_is_backfilled_from_the_example(self, tmp_path: Path):
        local = tmp_path / "content.local.json"
        local.write_text(json.dumps({"acts": {"zeta": ["zeta"]}}), encoding="utf-8")
        result = load_content(local, EXAMPLE_CONTENT)
        assert result["clip_jump_phrases"] == _example()["clip_jump_phrases"]

    def test_missing_acts_are_backfilled_from_the_example(self, tmp_path: Path):
        local = tmp_path / "content.local.json"
        local.write_text(json.dumps({"clip_jump_phrases": ["skip ahead"]}), encoding="utf-8")
        result = load_content(local, EXAMPLE_CONTENT)
        assert result["acts"] == _example()["acts"]

    def test_a_present_key_is_not_overwritten_by_the_example(self, tmp_path: Path):
        local = tmp_path / "content.local.json"
        local.write_text(
            json.dumps({"clip_jump_phrases": ["skip ahead", "rewind that"]}),
            encoding="utf-8",
        )
        result = load_content(local, EXAMPLE_CONTENT)
        assert result["clip_jump_phrases"] == ["skip ahead", "rewind that"]

    def test_missing_web_providers_defaults_to_empty_not_the_example(self, tmp_path: Path):
        assert _example()["web_providers"], "example is expected to define providers"
        local = tmp_path / "content.local.json"
        local.write_text(json.dumps({"acts": {"zeta": ["zeta"]}}), encoding="utf-8")
        result = load_content(local, EXAMPLE_CONTENT)
        assert result["web_providers"] == []

    def test_an_overlay_that_will_not_parse_says_which_file_it_is(self, tmp_path: Path):
        local = tmp_path / "content.local.json"
        local.write_text('{"acts": [', encoding="utf-8")

        with pytest.raises(ValueError, match=re.escape(str(local))):
            load_content(local, EXAMPLE_CONTENT)


class TestGenauFlickNoun:
    def test_the_settings_all_of_haglio_shares_name_genaus_flicks(self, tmp_path: Path, monkeypatch):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"genau_flick_words": {"one": "pip", "many": "pips"}}),
                            encoding="utf-8")
        monkeypatch.setenv(haglio_settings.SETTINGS_ENV, str(settings))

        assert genau_flick_noun() == Noun(one="pip", many="pips")


class TestLoadWebProviders:
    def test_absent_local_uses_the_example_providers(self, tmp_path: Path):
        providers = load_web_providers(tmp_path / "missing.json", EXAMPLE_CONTENT)
        assert len(providers) == len(_example()["web_providers"])

    def test_the_overlay_is_parsed_once_however_often_the_providers_are_asked_for(self, tmp_path: Path):
        local = tmp_path / "content.local.json"
        local.write_text(json.dumps({"web_providers": [{"marker": "m", "gallery_url": "https://h/{id}"}]}),
                         encoding="utf-8")
        first = load_web_providers(local, EXAMPLE_CONTENT)
        local.write_text(json.dumps({"web_providers": []}), encoding="utf-8")
        assert load_web_providers(local, EXAMPLE_CONTENT) is first

    def test_overlay_omitting_web_providers_yields_none(self, tmp_path: Path):
        local = tmp_path / "content.local.json"
        local.write_text(json.dumps({"acts": {"zeta": ["zeta"]}}), encoding="utf-8")
        assert load_web_providers(local, EXAMPLE_CONTENT) == ()


def test_importing_the_vocabulary_reads_no_overlay():
    probe = (
        "import fun_time.content, fun_time.filter_vocab, fun_time.media_actions; "
        "print(fun_time.content.load_content.cache_info().currsize)"
    )
    result = subprocess.run([sys.executable, "-c", probe],
                            capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "0", (
        "importing these parsed the content overlay; have them call "
        "load_content()/load_acts()/load_web_providers() instead"
    )
