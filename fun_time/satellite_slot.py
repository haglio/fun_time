"""One satellite's launch bundle — built once where the manifest is read and
passed whole down the startup chain, instead of as flat portrait_/landscape_
parameter pairs at every level.  :func:`for_player` refuses a swapped slot."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .players import Player
from .window_layout import WindowRect


@dataclass(frozen=True)
class SatelliteSlot:
    player: Player
    sources: str
    cmd_file: str | Path
    paused_file: str | Path
    status_file: str | Path
    log_file: str | Path
    playlist_file: str | Path
    rect: WindowRect
    hud_file: str | Path | None = None


def for_player(slot: SatelliteSlot, player: Player) -> SatelliteSlot:
    """*slot*, after refusing one that belongs to the other player."""
    if slot.player is not player:
        raise ValueError(f"a {slot.player.label} slot was handed to {player.label}")
    return slot
