"""Genau's numbers, read off the genau section of Fun Time's config."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from fun_time.config import EXAMPLE_CONFIG_PATH
from fun_time.genau_config import GenauSettings


def _write(tmp_path: Path, payload) -> Path:
    path = tmp_path / "fun_time_config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_example_config_names_each_number_at_its_default():
    section = json.loads(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))["genau"]

    assert set(section) == {field.name for field in dataclasses.fields(GenauSettings)}
    assert GenauSettings.read(EXAMPLE_CONFIG_PATH) == GenauSettings()


class TestReadingTheGenauSection:
    def test_the_genau_section_is_what_is_read(self, tmp_path):
        path = _write(tmp_path, {"genau": {
            "beats_per_loop": 2.0, "bpm_smoothing": 0.5, "sync_strength": 0.1,
            "flick_cache_size": 4, "shuffle_on_load": False,
            "udp_host": "127.0.0.2", "udp_port": 50999,
        }})

        settings = GenauSettings.read(path)

        assert settings == GenauSettings(
            beats_per_loop=2.0, bpm_smoothing=0.5, sync_strength=0.1,
            flick_cache_size=4, shuffle_on_load=False,
            udp_host="127.0.0.2", udp_port=50999,
        )

    def test_a_key_the_file_leaves_out_keeps_genaus_own_default(self, tmp_path):
        path = _write(tmp_path, {"genau": {"beats_per_loop": 3.0}})

        settings = GenauSettings.read(path)

        assert settings.beats_per_loop == 3.0
        assert settings.bpm_smoothing == GenauSettings().bpm_smoothing
        assert settings.udp_port == GenauSettings().udp_port

    def test_a_config_still_naming_genaus_old_settings_file_is_read_from_that_file(self, tmp_path):
        """Until a config holds the numbers itself, they are where Genau's checkout
        kept them, so a session plays on what it played on before they moved."""
        old = tmp_path / "genau_config.json"
        old.write_text(json.dumps({"genau": {"beats_per_loop": 2.0}}), encoding="utf-8")
        path = _write(tmp_path, {"paths": {"genau_config_path": str(old)},
                                 "genau": {"beats_per_loop": 3.0}})

        assert GenauSettings.read(path).beats_per_loop == 2.0

    @pytest.mark.parametrize("payload", [{}, {"genau": "not a section"}, [1, 2]])
    def test_a_config_with_no_genau_section_is_the_defaults(self, tmp_path, payload):
        assert GenauSettings.read(_write(tmp_path, payload)) == GenauSettings()

    def test_a_missing_or_unreadable_file_is_the_defaults(self, tmp_path):
        """A VR session must not fail to start over a number it has a good
        default for."""
        assert GenauSettings.read(tmp_path / "absent.json") == GenauSettings()
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert GenauSettings.read(broken) == GenauSettings()
        assert GenauSettings.read(None) == GenauSettings()


class TestTheRideThroughTheManifest:
    def test_what_goes_out_comes_back_the_same(self):
        settings = GenauSettings(
            beats_per_loop=2.5, bpm_smoothing=0.2, sync_strength=0.4,
            flick_cache_size=3, shuffle_on_load=False,
            udp_host="127.0.0.9", udp_port=50560,
        )

        assert GenauSettings.from_manifest(settings.manifest_fields()) == settings

    def test_a_manifest_from_before_these_keys_reads_as_the_defaults(self):
        assert GenauSettings.from_manifest({}) == GenauSettings()

    def test_every_field_rides_as_a_string(self):
        """The manifest is an INI: configparser takes strings and nothing else."""
        assert all(isinstance(v, str) for v in GenauSettings().manifest_fields().values())
