"""One satellite's launch bundle, built once where the manifest is read and
passed whole down the startup chain.  :func:`for_funestra` refuses a swap.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from satellite.contract import SatelliteChannels

from .room_funestras import RoomFunestra
from .window_layout import WindowRect


@dataclass(frozen=True)
class SatelliteSlot:
    funestra: RoomFunestra
    sources: str
    channels: SatelliteChannels
    log_file: str | Path
    rect: WindowRect


def for_funestra(slot: SatelliteSlot, funestra: RoomFunestra) -> SatelliteSlot:
    """*slot*, after refusing one that belongs to the other Funestra."""
    if slot.funestra is not funestra:
        raise ValueError(f"a {slot.funestra.label} slot was handed to {funestra.label}")
    return slot
