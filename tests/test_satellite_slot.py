"""SatelliteSlot — the launch chain's per-side bundle (finding B_orchestration/001)."""
from __future__ import annotations

from pathlib import Path

import pytest

from fun_time.players import Player
from fun_time.satellite_slot import SatelliteSlot, for_side
from fun_time.window_layout import WindowRect


def _slot(side: Player, tmp_path: Path) -> SatelliteSlot:
    return SatelliteSlot(
        side=side,
        sources=str(tmp_path / side.label),
        cmd_file=tmp_path / f"{side.label}_cmd.txt",
        paused_file=tmp_path / f"{side.label}_paused.txt",
        status_file=tmp_path / f"{side.label}_status.txt",
        log_file=tmp_path / f"{side.label}.log",
        playlist_file=tmp_path / f"{side.label}_playlist.tsv",
        rect=WindowRect(x=0, y=0, width=100, height=100),
    )


def test_for_side_hands_back_the_matching_slot(tmp_path: Path):
    slot = _slot(Player.PORTRAIT, tmp_path)
    assert for_side(slot, Player.PORTRAIT) is slot


def test_for_side_refuses_the_swap(tmp_path: Path):
    """A mis-wired side swaps the two players' command files — the launch
    path's own comments warn about exactly this — so it fails loudly instead."""
    with pytest.raises(ValueError, match="portrait slot was handed to landscape"):
        for_side(_slot(Player.PORTRAIT, tmp_path), Player.LANDSCAPE)
