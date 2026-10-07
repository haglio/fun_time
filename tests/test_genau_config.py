from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.genau_config import genaus_own_logs


@pytest.mark.parametrize("config_text", [
    None,
    "{ not json",
    '{"paths": {"state_dir": "state"}}',
    '["state_dir"]',
    '{"state_dir": 7}',
])
def test_a_genau_config_this_session_cannot_read_names_no_logs(tmp_path: Path, config_text):
    config = tmp_path / "genau_config.json"
    if config_text is not None:
        config.write_text(config_text, encoding="utf-8")

    assert genaus_own_logs(config) == ()
