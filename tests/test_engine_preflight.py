"""The engine preflight: a missing video engine shouts once here, at launch,
instead of dying silently in every player and leaving him an empty room."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import fun_time.orchestrator as desktop
import fun_time_vr.orchestrator as headset
from fun_time.engine_preflight import (
    engine_missing_abort,
    engine_preflight_error,
    player_interpreters,
)


def _runner(returncodes):
    """A fake ``subprocess.run`` returning the given exit codes, call by call."""
    seq = iter(returncodes)
    calls = []

    def run(cmd, **_kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, next(seq), stdout="", stderr="")

    run.calls = calls
    return run


def test_no_error_when_every_interpreter_loads_the_engine():
    run = _runner([0, 0])
    interpreters = {
        "the main player": Path("genau/python.exe"),
        "the side players": Path("fun_time/python.exe"),
    }

    assert engine_preflight_error(interpreters, run=run) is None
    assert len(run.calls) == 2


def test_error_names_the_player_whose_interpreter_cannot_load_the_engine():
    run = _runner([1])  # its interpreter cannot import the engine
    interpreters = {"the main player": Path("genau/python.exe")}

    error = engine_preflight_error(interpreters, run=run)

    assert error is not None
    assert "the main player" in error
    assert "fetch_libmpv" in error


def test_the_first_failing_interpreter_stops_the_check():
    run = _runner([1, 0])  # would raise StopIteration if it ran the second
    interpreters = {
        "the main player": Path("genau/python.exe"),
        "the side players": Path("fun_time/python.exe"),
    }

    assert engine_preflight_error(interpreters, run=run) is not None
    assert len(run.calls) == 1


def test_the_probe_actually_loads_the_engine():
    # A probe that stopped importing mpv would pass forever over a dead engine.
    run = _runner([0])

    engine_preflight_error({"the main player": Path("p.exe")}, run=run)

    cmd = run.calls[0]
    assert cmd[1] == "-c"
    assert "_import_mpv()" in cmd[2]


def test_player_interpreters_maps_each_player_to_its_own_venv_python():
    paths = SimpleNamespace(
        python_exe=Path("fun_time/py.exe"),
        genau_python_exe=Path("genau/py.exe"),
    )

    assert player_interpreters(paths) == {
        "the main player": Path("genau/py.exe"),
        "the side players": Path("fun_time/py.exe"),
    }


def test_player_interpreters_omits_the_main_player_without_a_genau_python():
    paths = SimpleNamespace(python_exe=Path("fun_time/py.exe"), genau_python_exe=None)

    assert player_interpreters(paths) == {"the side players": Path("fun_time/py.exe")}


def test_engine_missing_abort_alerts_and_aborts_when_a_player_cannot_load():
    config = SimpleNamespace(
        paths=SimpleNamespace(python_exe=Path("p.exe"), genau_python_exe=None)
    )
    shown: list[str] = []

    aborted = engine_missing_abort(config, run=_runner([1]), alert=shown.append)

    assert aborted is True
    assert shown and "libmpv" in shown[0]


def test_engine_missing_abort_is_false_and_silent_when_every_player_loads():
    config = SimpleNamespace(
        paths=SimpleNamespace(python_exe=Path("p.exe"), genau_python_exe=None)
    )
    shown: list[str] = []

    aborted = engine_missing_abort(config, run=_runner([0]), alert=shown.append)

    assert aborted is False
    assert shown == []


def test_both_the_desktop_and_headset_launches_gate_on_the_preflight():
    # A dead engine takes the headset's players down exactly as it does the
    # desktop's, so both entry points must refuse rather than open onto empty
    # windows -- the desktop/headset pairing the engineering law now requires.

    for entry_point in (desktop, headset):
        source = Path(entry_point.__file__).read_text(encoding="utf-8")
        assert "engine_missing_abort(config" in source
