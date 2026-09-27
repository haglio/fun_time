"""A player that dies while the room is coming up ends the launch, saying which one
and what it said -- rather than every window and status wait running out and leaving
him a room with no players in it and Ctrl+Alt+Q as the only way out (2026-09-22)."""
from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.player_deaths import (
    LaunchedPlayer,
    PlayerDied,
    raise_if_a_player_died,
)


def _alive(*living: int):
    return lambda pid: pid in living


def test_a_living_room_raises_nothing():
    players = [LaunchedPlayer("the Portrait player", 30), LaunchedPlayer("Genau", 60)]

    raise_if_a_player_died(players, alive=_alive(30, 60))


def test_the_player_that_died_is_named_with_what_it_said(tmp_path: Path):
    log = tmp_path / "main_player.log"
    log.write_text(
        "2026-09-26 01:47:03 opening the library\n"
        "Traceback (most recent call last):\n"
        '  File "<string>", line 1, in <module>\n'
        "OSError: The engine (libmpv) could not be loaded. Looked in: "
        "C:\\player_core\\vendor (no libmpv-2.dll in it)\n",
        encoding="utf-8")
    players = [LaunchedPlayer("the Portrait player", 30),
               LaunchedPlayer("the Main player", 25, log)]

    with pytest.raises(PlayerDied) as died:
        raise_if_a_player_died(players, alive=_alive(30))

    assert died.value.player.name == "the Main player"
    assert "The engine (libmpv) could not be loaded" in died.value.said
