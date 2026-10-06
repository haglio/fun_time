from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from player_core.playlist import read_playlist

from fun_time import polled_files
from fun_time.modes import write_playlist_file
from fun_time.runtime_flow import write_flag_file
from fun_time.satellites_mode import ORIGENERATOR_MODE
from fun_time.shared_state import BridgeState, read_shared_state, write_shared_state


@contextmanager
def _held_open_for(path: Path, seconds: float):
    reader = path.open(encoding="utf-8")
    release = threading.Timer(seconds, reader.close)
    release.start()
    try:
        yield
    finally:
        release.join()


def test_a_playlist_lands_though_a_player_is_partway_through_reading_it(tmp_path: Path):
    playlist = tmp_path / "portrait_playlist.tsv"
    playlist.write_text(f"{tmp_path / 'before.mp4'}\n", encoding="utf-8")

    with _held_open_for(playlist, 0.1):
        write_playlist_file(playlist, [str(tmp_path / "after.mp4")])

    assert [item.path for item in read_playlist(playlist)] == [tmp_path / "after.mp4"]


def test_a_playlist_a_player_keeps_open_past_the_budget_is_refused_out_loud(
        tmp_path: Path, monkeypatch):
    playlist = tmp_path / "portrait_playlist.tsv"
    playlist.write_text(f"{tmp_path / 'before.mp4'}\n", encoding="utf-8")
    monkeypatch.setattr(polled_files, "READER_HOLD_BUDGET_S", 0.05)

    with _held_open_for(playlist, 0.5), pytest.raises(OSError, match="portrait_playlist"):
        write_playlist_file(playlist, [str(tmp_path / "after.mp4")])


def test_a_flag_lands_though_a_player_is_partway_through_reading_it(tmp_path: Path):
    flag = tmp_path / "main_player_paused.txt"
    flag.write_text("0", encoding="utf-8")

    with _held_open_for(flag, 0.1):
        write_flag_file(flag, True)

    assert flag.read_text(encoding="utf-8") == "1"


def test_a_flag_a_player_keeps_open_past_the_budget_is_reported(tmp_path: Path, monkeypatch, caplog):
    flag = tmp_path / "landscape_paused.txt"
    flag.write_text("0", encoding="utf-8")
    monkeypatch.setattr(polled_files, "READER_HOLD_BUDGET_S", 0.05)

    with _held_open_for(flag, 0.5), caplog.at_level(logging.WARNING, logger="fun_time.runtime_flow"):
        write_flag_file(flag, True)

    assert "landscape_paused.txt" in caplog.text


def test_the_room_state_lands_though_a_reader_is_partway_through_reading_it(tmp_path: Path):
    state_file = tmp_path / "shared_bridge_state.ini"
    write_shared_state(state_file, BridgeState())

    with _held_open_for(state_file, 0.1):
        write_shared_state(state_file, BridgeState(satellites_mode=ORIGENERATOR_MODE))

    assert read_shared_state(state_file).satellites_mode == ORIGENERATOR_MODE
