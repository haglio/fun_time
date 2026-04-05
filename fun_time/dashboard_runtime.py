from __future__ import annotations

import configparser
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DashboardPanelSnapshot:
    path: str
    locked: bool = False


@dataclass(frozen=True)
class DashboardWindowSnapshot:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class DashboardSnapshot:
    f_mode_enabled: bool
    primary_uses_genau: bool
    osr2_mode: str
    mfp_alive: bool
    primary_responsive: bool
    omni_paused: bool
    primary: DashboardPanelSnapshot
    portrait: DashboardPanelSnapshot
    landscape: DashboardPanelSnapshot
    window: DashboardWindowSnapshot
    voice_active: bool = True


def load_dashboard_snapshot(path: Path) -> DashboardSnapshot | None:
    if not path.exists():
        return None

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string(_read_dashboard_text(path))
    if not parser.sections():
        return None

    return DashboardSnapshot(
        f_mode_enabled=_read_bool(parser, "fmode", "enabled"),
        primary_uses_genau=_read_bool(parser, "primary", "uses_genau"),
        osr2_mode=parser.get("osr2", "mode", fallback="controlled"),
        mfp_alive=_read_bool(parser, "mfp", "alive"),
        primary_responsive=_read_bool(parser, "primary", "responsive"),
        omni_paused=_read_bool(parser, "omnipause", "active"),
        voice_active=_read_bool(parser, "voice", "active") if parser.has_section("voice") else True,
        primary=_read_panel(parser, "primary"),
        portrait=_read_panel(parser, "portrait"),
        landscape=_read_panel(parser, "landscape"),
        window=_read_window(parser),
    )


def is_osr2_device_on(path: Path, *, max_age_seconds: float = 16.0, now: float | None = None) -> bool:
    if not path.exists():
        return False
    try:
        last_rx = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    current = time.time() if now is None else now
    return (current - last_rx) < max_age_seconds


def is_broker_heartbeat_fresh(path: Path, *, max_age_seconds: float = 3.0, now: float | None = None) -> bool:
    if not path.exists():
        return False
    try:
        heartbeat = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return False
    current = time.time() if now is None else now
    return (current - heartbeat) <= max_age_seconds


def _read_dashboard_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("dashboard_state", raw, 0, 1, "unable to decode dashboard snapshot")


def _read_bool(parser: configparser.ConfigParser, section: str, option: str) -> bool:
    return parser.get(section, option, fallback="0").strip() not in {"", "0", "false", "False"}


def _read_panel(parser: configparser.ConfigParser, section: str) -> DashboardPanelSnapshot:
    return DashboardPanelSnapshot(
        path=parser.get(section, "path", fallback=""),
        locked=_read_bool(parser, section, "locked"),
    )


def _read_window(parser: configparser.ConfigParser) -> DashboardWindowSnapshot:
    return DashboardWindowSnapshot(
        x=parser.getint("window", "x", fallback=0),
        y=parser.getint("window", "y", fallback=0),
        width=parser.getint("window", "width", fallback=0),
        height=parser.getint("window", "height", fallback=0),
    )
