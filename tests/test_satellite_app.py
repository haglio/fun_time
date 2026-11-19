from __future__ import annotations

import satellite.app
import satellite.silence
from satellite.contract import SATELLITE_CHANNELS


def _the_program_as_a_satellite_runs_it(monkeypatch, argv: list[str]) -> list[tuple]:
    handed = []
    monkeypatch.setattr(satellite.app, "run_the_funestra_program", lambda argv, **asked: handed.append(
        (argv, asked)) or 0)

    assert satellite.app.main(argv) == 0
    return handed


def test_a_satellite_runs_the_funestra_program_on_its_own_channels(monkeypatch):
    monkeypatch.setenv("FUN_TIME_MUTE_AUDIO", "1")
    monkeypatch.setattr(satellite.silence, "on_hidden_desktop", lambda: False)

    handed = _the_program_as_a_satellite_runs_it(monkeypatch, ["--title", "Made Up Funestra"])

    assert handed == [(["--title", "Made Up Funestra"],
                       {"channels": SATELLITE_CHANNELS, "silent": True})]


def test_a_satellite_plays_its_sound_while_fun_time_leaves_it_on(monkeypatch, unmuted):
    monkeypatch.setattr(satellite.silence, "on_hidden_desktop", lambda: False)

    [(_argv, asked)] = _the_program_as_a_satellite_runs_it(monkeypatch, [])

    assert asked["silent"] is False
