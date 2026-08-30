"""The panel's own snapshot: what the bar draws, and where its window sits.

The dispatch loop writes this INI every tick and the panel reads it back.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from .dashboard_bridge import decode_snapshot


@dataclass(frozen=True)
class DashboardWindowSnapshot:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class DashboardSnapshot:
    omni_paused: bool
    window: DashboardWindowSnapshot
    voice_active: bool = True


def load_dashboard_snapshot(path: Path) -> DashboardSnapshot | None:
    if not path.exists():
        return None

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string(decode_snapshot(path.read_bytes()))
    if not parser.sections():
        return None

    return DashboardSnapshot(
        omni_paused=_read_bool(parser, "omnipause", "active"),
        voice_active=_read_bool(parser, "voice", "active") if parser.has_section("voice") else True,
        window=_read_window(parser),
    )


def _read_bool(parser: configparser.ConfigParser, section: str, option: str) -> bool:
    return parser.get(section, option, fallback="0").strip() not in {"", "0", "false", "False"}


def _read_window(parser: configparser.ConfigParser) -> DashboardWindowSnapshot:
    return DashboardWindowSnapshot(
        x=parser.getint("window", "x", fallback=0),
        y=parser.getint("window", "y", fallback=0),
        width=parser.getint("window", "width", fallback=0),
        height=parser.getint("window", "height", fallback=0),
    )
