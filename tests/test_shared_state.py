from __future__ import annotations

import ast
import configparser
import os
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest
from funestra.hud_placement import HudCorner
from funestra.modes import MainMode

from fun_time import shared_state
from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.mode_plan import MAIN_KINO_MODE, MAIN_MODES, build_mode_switch_plan
from fun_time.shared_state import (
    _LAST_SESSIONS_KEYS,
    SHARED_STATE_FILENAME,
    BridgeState,
    SatelliteState,
    main_snippet_bounds,
    read_shared_state,
    shared_state_path,
    write_shared_state,
)
from tests.made_up_state import shifted_state


def test_the_state_file_is_named_off_the_state_dir(tmp_path: Path):
    assert shared_state_path(tmp_path) == tmp_path / SHARED_STATE_FILENAME


def test_a_write_that_meets_a_reader_holding_the_file_open_still_lands(tmp_path: Path, monkeypatch):
    state_file = tmp_path / SHARED_STATE_FILENAME
    real_replace = os.replace
    refused: list[str] = []

    def replace_refused_once(src, dst):
        if not refused:
            refused.append(str(dst))
            raise PermissionError(
                32, "The process cannot access the file because it is being used by another process")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace_refused_once)
    write_shared_state(state_file, BridgeState(omni_paused=True))

    assert refused
    assert read_shared_state(state_file).omni_paused is True


def test_every_field_survives_the_round_trip_the_snippet_makes_each_tick(tmp_path: Path):
    state_file = tmp_path / "shared_state.ini"
    state = shifted_state(BridgeState())

    write_shared_state(state_file, state)

    assert read_shared_state(state_file) == state


class TestSharedState:
    def test_write_then_read_roundtrip(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(
            portrait=SatelliteState(hud_minimized=True),
            landscape=SatelliteState(volume=30),
            main_mode=MainMode.GENAU,
            main_scripted_filter=True,
            omni_paused=True,
        )

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded == state

    def test_state_that_was_never_written_reads_as_absent(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        assert read_shared_state(state_file) is None

    def test_active_funestra_roundtrips(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        write_shared_state(state_file, BridgeState(active_funestra=3))

        loaded = read_shared_state(state_file)

        assert loaded is not None
        assert loaded.active_funestra == 3

    def test_active_funestra_defaults_to_the_primary_for_legacy_files(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        state_file.write_text(
            "[state]\nportrait_locked = 0\nlandscape_locked = 0\nmain_mode = video\n"
            "omni_paused = 0\n",
            encoding="utf-8",
        )

        loaded = read_shared_state(state_file)

        assert loaded is not None
        assert loaded.active_funestra == 1

    def test_roundtrip_preserves_the_sound_level(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"

        write_shared_state(state_file, BridgeState(volume=30, muted=True))
        loaded = read_shared_state(state_file)

        assert loaded.volume == 30
        assert loaded.muted is True


    def test_a_switch_that_starts_on_reads_on_from_a_file_written_before_it(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        state_file.write_text("[state]\nmain_mode = video\n", encoding="utf-8")

        loaded = read_shared_state(state_file)

        assert (loaded.main_plays_vr, loaded.main_plays_flat) == (True, True)


def test_the_state_file_sits_under_both_startup_and_the_dispatch_loop():
    both_write_it = ("fun_time.session_resume", "fun_time.windows_bridge_dispatch_loop",
                     "fun_time.command_dispatch")
    probe = (
        "import sys, fun_time.shared_state; "
        f"print(','.join(m for m in {both_write_it!r} if m in sys.modules) or '-'); "
        "print(len([m for m in sys.modules if m.startswith('fun_time.')]))"
    )
    result = subprocess.run([sys.executable, "-c", probe],
                            capture_output=True, text=True, check=True)
    pulled_in, count = result.stdout.split()

    assert pulled_in == "-", f"fun_time.shared_state imports what writes it: {pulled_in}"
    assert int(count) <= 10, (
        f"reading the shared state file now imports {count} fun_time modules; startup, the "
        "dashboard and the headset each read it from a process that wants none of the "
        "dispatcher — lower this ceiling when it drops, never raise it")


def test_the_round_trip_spells_no_field_of_the_record_by_hand():
    spelled_out = sorted(
        {node.value for node in ast.walk(ast.parse(
            Path(shared_state.__file__).read_text(encoding="utf-8")))
         if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        & {f.name for f in fields(BridgeState)})

    written_today = {key for keys in _LAST_SESSIONS_KEYS.values() for key in keys}
    assert spelled_out == sorted(written_today & {f.name for f in fields(BridgeState)}), (
        "the round trip names these fields by hand: " + ", ".join(spelled_out)
        + " — derive them from the record instead, so one added to it is carried: a field "
        "spelled in the record, the writer and the reader as three lists was saved never or "
        "reset at every resume when one list fell behind."
    )


def test_no_satellite_value_is_a_field_of_the_whole_state():
    stray = [f.name for f in fields(BridgeState)
             if f.name.startswith(("portrait_", "landscape_"))
             or f.name[-1:] in ("2", "3")]

    assert stray == [], (
        "these say a satellite's name in the state record: " + ", ".join(stray)
        + " — put them on SatelliteState and reach them through BridgeState.satellite: "
        "a field of its own beside the session's is one more place a 2 is written where a 3 belongs."
    )


class TestTheSideLens:
    def test_satellite_reads_the_state_of_that_satellite_alone(self):
        state = BridgeState(
            portrait=SatelliteState(hud_corner=HudCorner.LOWER_RIGHT, hud_minimized=True,
                                    volume=40, muted=False),
            landscape=SatelliteState(volume=60),
        )
        portrait = state.satellite(FunTimeFunestra.PORTRAIT)
        assert (portrait.hud_corner, portrait.hud_minimized) == (HudCorner.LOWER_RIGHT, True)
        assert (portrait.volume, portrait.muted) == (40, False)
        landscape = state.satellite(FunTimeFunestra.LANDSCAPE)
        assert landscape.volume == 60
        assert landscape.hud_minimized is False

    def test_with_satellite_leaves_the_other_satellite_where_it_was(self):
        state = BridgeState(landscape=SatelliteState(volume=60))

        state = state.with_satellite(FunTimeFunestra.PORTRAIT, hud_minimized=True, volume=40)

        assert state.satellite(FunTimeFunestra.PORTRAIT) == SatelliteState(
            hud_minimized=True, volume=40)
        assert state.satellite(FunTimeFunestra.LANDSCAPE) == SatelliteState(volume=60)

    def test_with_satellite_refuses_a_name_no_satellite_carries(self):
        with pytest.raises(TypeError):
            BridgeState().with_satellite(FunTimeFunestra.PORTRAIT, lokced=True)

    def test_a_default_satellite_state_means_the_satellite_sits_at_its_defaults(self):
        assert BridgeState().satellite(FunTimeFunestra.LANDSCAPE) == SatelliteState()
        assert BridgeState(landscape=SatelliteState(hud_minimized=True)).satellite(
            FunTimeFunestra.LANDSCAPE) != SatelliteState()

    def test_the_lens_survives_the_ini_round_trip(self, tmp_path):
        state_file = tmp_path / "shared_bridge_state.ini"
        state = BridgeState().with_satellite(
            FunTimeFunestra.LANDSCAPE, hud_corner=HudCorner.LOWER_LEFT, volume=35, muted=False)

        write_shared_state(state_file, state)

        assert read_shared_state(state_file).satellite(FunTimeFunestra.LANDSCAPE) == state.satellite(
            FunTimeFunestra.LANDSCAPE)


def test_an_old_mode_word_comes_back_as_a_mode_the_switch_plan_accepts(tmp_path: Path):
    state_file = tmp_path / SHARED_STATE_FILENAME
    for saved_main in ("main_player", "hybrid", "video"):
        write_shared_state(state_file, BridgeState(main_mode=saved_main))

        resumed = read_shared_state(state_file)

        assert resumed.main_mode == MAIN_KINO_MODE, saved_main
        build_mode_switch_plan(current_mode=resumed.main_mode, target_mode=MainMode.GENAU,
                               omni_paused=False)


@pytest.mark.parametrize("satellite", ["portrait", "landscape"])
@pytest.mark.parametrize("saved", ["kino", "origenerator", "video", "player"])
def test_a_satellite_mode_an_older_session_saved_is_read_past(tmp_path: Path, satellite, saved):
    state_file = tmp_path / SHARED_STATE_FILENAME
    state_file.write_text(f"[state]\n{satellite}_mode = {saved}\nmax_intensity = 38\n",
                          encoding="utf-8")

    assert read_shared_state(state_file) == BridgeState(max_intensity=38)


def test_a_satellite_mode_an_older_session_saved_is_gone_from_the_next_file_written(
        tmp_path: Path):
    state_file = tmp_path / SHARED_STATE_FILENAME
    state_file.write_text("[state]\nportrait_mode = origenerator\nlandscape_mode = kino\n",
                          encoding="utf-8")

    write_shared_state(state_file, read_shared_state(state_file))

    written = state_file.read_text(encoding="utf-8")
    assert "portrait_mode" not in written
    assert "landscape_mode" not in written


def test_a_mode_this_app_still_has_is_read_back_unchanged(tmp_path: Path):
    state_file = tmp_path / SHARED_STATE_FILENAME
    for mode in MAIN_MODES:
        write_shared_state(state_file, BridgeState(main_mode=mode))
        assert read_shared_state(state_file).main_mode == mode


_EXPECTED_STATE_KEYS = {
    "main_mode": "kino",
    "rfb_mode": "rfb",
    "origenerator_core_ready": "0",
    "main_scripted_filter": "0",
    "main_hud_corner": "upper_left", "main_hud_edge": "lower", "main_hud_minimized": "0",
    "portrait_hud_corner": "upper_left", "landscape_hud_corner": "upper_left",
    "portrait_hud_edge": "lower", "landscape_hud_edge": "lower",
    "portrait_hud_minimized": "0", "landscape_hud_minimized": "0",
    "omni_paused": "0",
    "main_latest": "0",
    "main_shuffle_seed": "0", "main_length_mode": "mixed",
    "main_enhancement": "both",
    "main_plays_vr": "1", "main_plays_flat": "1",
    "main_plays_portrait": "1", "main_plays_landscape": "1",
    "genau_latest": "0", "genau_plays_vr": "1", "genau_plays_flat": "1",
    "genau_plays_portrait": "1", "genau_plays_landscape": "1",
    "genau_favorites_filter": "0",
    "active_funestra": "1", "core_named_last": "0",
    "volume": "100", "muted": "0", "max_intensity": "100",
    "osr2_control": "driving",
    "crowned": "main", "majority": "portrait",
    "osr2_funestra": "1",
    "main_speed": "1",
    "portrait_volume": "100", "landscape_volume": "100", "portrait_muted": "1",
    "landscape_muted": "1",
    "main_locked": "1", "main_snippet_video": "", "main_snippet_bounds_ms": "",
    "genau_speed": "50", "genau_amplitude": "100",
    "genau_center": "50", "genau_shape": "sine",
    "mic_muted": "0",
    "headset_layout": "",
    "genau_flick_seconds": "10", "genau_learned": "0", "genau_locked": "1",
}


def test_the_file_carries_exactly_these_keys_spelled_exactly_this_way(tmp_path: Path):
    state_file = tmp_path / SHARED_STATE_FILENAME
    write_shared_state(state_file, BridgeState())

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(state_file, encoding="utf-8")

    assert dict(parser["state"]) == _EXPECTED_STATE_KEYS, (
        "the dashboard and the resume read this file by key name: a key respelled here comes "
        "back at its default with nothing said, so rename it here in the same commit")


def test_a_number_a_hand_edit_spoiled_reads_as_that_fields_default(tmp_path):
    state_file = tmp_path / "shared_state.ini"
    state_file.write_text("[state]\nmain_mode = video\nvolume = loud\n", encoding="utf-8")

    loaded = read_shared_state(state_file)

    assert loaded is not None
    assert loaded.volume == BridgeState().volume


def test_main_snippet_bounds_read_back_off_the_remembered_value():
    assert main_snippet_bounds(BridgeState(main_snippet_bounds_ms="1000 5000")) == (1000, 5000)


def test_main_snippet_bounds_are_none_when_the_value_is_empty_or_hand_edited():
    assert main_snippet_bounds(BridgeState()) == (0, 0)
    assert main_snippet_bounds(BridgeState(main_snippet_bounds_ms="soon")) == (0, 0)


def test_a_file_from_before_a_satellite_had_one_mode_loads_and_drops_what_it_no_longer_keeps(
        tmp_path: Path):
    state_file = tmp_path / SHARED_STATE_FILENAME
    gone = ("locked", "filter", "favorites_filter", "enhancement", "latest", "loop",
            "nav_map_anchor", "widen_video", "nav_anchor", "handed_over", "mode", "speed")
    state_file.write_text("[state]\nmain_mode = kino\nportrait_hud_corner = lower_right\n" + "".join(
        f"{satellite}_{name} = 1\n" for satellite in ("portrait", "landscape") for name in gone),
        encoding="utf-8")

    loaded = read_shared_state(state_file)
    write_shared_state(state_file, loaded)

    assert loaded.portrait == SatelliteState(hud_corner=HudCorner.LOWER_RIGHT)
    written = state_file.read_text(encoding="utf-8")
    assert not any(f"{satellite}_{name} " in written
                   for satellite in ("portrait", "landscape") for name in gone)
