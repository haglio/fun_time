from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from funestra.genau_settings import GenauSettings

from fun_time.config import EXAMPLE_CONFIG_PATH


def test_the_example_config_names_each_number_at_its_default():
    section = json.loads(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))["genau"]

    assert set(section) == {field.name for field in dataclasses.fields(GenauSettings)}
    assert GenauSettings.read(EXAMPLE_CONFIG_PATH) == GenauSettings()


def test_the_settings_file_genaus_checkout_kept_is_never_read(tmp_path: Path):
    old = tmp_path / "genau_config.json"
    old.write_text(json.dumps({"genau": {"beats_per_loop": 2.0}}), encoding="utf-8")
    config = tmp_path / "fun_time_config.json"
    config.write_text(json.dumps({"paths": {"genau_config_path": str(old)},
                                  "genau": {"beats_per_loop": 3.0}}), encoding="utf-8")

    assert GenauSettings.read(config).beats_per_loop == 3.0
