from __future__ import annotations

import os


def test_a_unit_test_runs_muted_unless_it_switches_the_mute_off():
    assert os.environ["FUN_TIME_MUTE_AUDIO"] == "1"
