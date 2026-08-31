"""One satellite's launch bundle — built once where the manifest is read and
passed whole down the startup chain, instead of as flat portrait_/landscape_
parameter pairs at every level.  :func:`for_side` refuses a swapped slot."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .players import Player
from .window_layout import WindowRect


@dataclass(frozen=True)
class SatelliteSlot:
    side: Player
    sources: str
    cmd_file: str | Path
    paused_file: str | Path
    status_file: str | Path
    log_file: str | Path
    playlist_file: str | Path
    rect: WindowRect
    hud_file: str | Path | None = None


def for_side(slot: SatelliteSlot, side: Player) -> SatelliteSlot:
    """*slot*, after refusing one that belongs to the other side."""
    if slot.side is not side:
        raise ValueError(f"a {slot.side.label} slot was handed to {side.label}")
    return slot
