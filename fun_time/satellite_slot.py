"""One satellite's launch bundle, built once where the manifest is read and
passed whole down the startup chain.  :func:`for_player` refuses a swap.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from satellite.contract import SatelliteChannels

from .players import Player
from .window_layout import WindowRect


@dataclass(frozen=True)
class SatelliteSlot:
    player: Player
    sources: str
    channels: SatelliteChannels
    log_file: str | Path
    rect: WindowRect


def for_player(slot: SatelliteSlot, player: Player) -> SatelliteSlot:
    """*slot*, after refusing one that belongs to the other player."""
    if slot.player is not player:
        raise ValueError(f"a {slot.player.label} slot was handed to {player.label}")
    return slot
