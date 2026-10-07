"""A player that dies while the room is coming up ends the launch, saying which one
and what it said -- rather than every window and status wait running out and leaving
him a room with no players in it and Ctrl+Alt+Q as the only way out (2026-09-22)."""
from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.child_launch import open_child_log
from fun_time.player_deaths import (
    LaunchedPlayer,
    PlayerDied,
    logs_as_they_stand,
    raise_if_a_player_died,
)


def _alive(*living: int):
    return lambda pid: pid in living


def _write(log: Path, *lines: str) -> None:
    with log.open("a", encoding="utf-8") as handle:
        handle.writelines(f"{line}\n" for line in lines)


def _what_it_said(player: LaunchedPlayer) -> str:
    with pytest.raises(PlayerDied) as died:
        raise_if_a_player_died([player], alive=_alive())
    return died.value.said


def test_a_living_room_raises_nothing():
    players = [LaunchedPlayer("the Portrait player", 30), LaunchedPlayer("Genau", 60)]

    raise_if_a_player_died(players, alive=_alive(30, 60))


def test_the_player_that_died_is_named_with_what_it_said(tmp_path: Path):
    log = tmp_path / "main_player.log"
    logs = logs_as_they_stand(log)
    _write(log,
           "2026-09-26 01:47:03 opening the library",
           "Traceback (most recent call last):",
           '  File "<string>", line 1, in <module>',
           "OSError: The engine (libmpv) could not be loaded. Looked in: "
           "C:\\player_core\\vendor (no libmpv-2.dll in it)")
    players = [LaunchedPlayer("the Portrait player", 30),
               LaunchedPlayer("the Main player", 25, logs)]

    with pytest.raises(PlayerDied) as died:
        raise_if_a_player_died(players, alive=_alive(30))

    assert died.value.player.name == "the Main player"
    assert "The engine (libmpv) could not be loaded" in died.value.said


def test_only_what_a_player_wrote_since_its_launch_is_quoted(tmp_path: Path):
    log = tmp_path / "main_player.log"
    _write(log, "2026-09-25 22:10:41 the session before ended normally")
    logs = logs_as_they_stand(log)
    _write(log, "OSError: The engine (libmpv) could not be loaded.")

    said = _what_it_said(LaunchedPlayer("the Main player", 25, logs))

    assert said == "OSError: The engine (libmpv) could not be loaded."


def test_the_line_its_launch_opened_the_log_with_is_not_quoted(tmp_path: Path):
    log = tmp_path / "genau.log"
    logs = logs_as_they_stand(log)
    with open_child_log(log, ["a-venv/python.exe", "-m", "genau", "--config", "a.json"]) as handle:
        handle.write(b"ImportError: cannot import name 'a_made_up_name'\n")

    said = _what_it_said(LaunchedPlayer("Genau", 60, logs))

    assert said == "ImportError: cannot import name 'a_made_up_name'"


def test_a_log_its_launch_rolled_aside_is_read_from_the_top_of_the_new_one(tmp_path: Path):
    log = tmp_path / "main_player.log"
    _write(log, "2026-09-25 22:10:41 the sessions before filled this log up" * 20)
    logs = logs_as_they_stand(log)
    with open_child_log(log, ["a-venv/python.exe", "-m", "main_player"], max_bytes=100) as handle:
        handle.write(b"OSError: The engine (libmpv) could not be loaded.\n")

    said = _what_it_said(LaunchedPlayer("the Main player", 25, logs))

    assert said == "OSError: The engine (libmpv) could not be loaded."
