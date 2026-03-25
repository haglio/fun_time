from __future__ import annotations

import configparser
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
    broker_running: bool
    controller_running: bool
    f_mode_enabled: bool
    robot_link_enabled: bool
    primary_uses_robot_hand: bool
    osr2_mode: str
    mfp_connected: bool
    primary: DashboardPanelSnapshot
    portrait: DashboardPanelSnapshot
    landscape: DashboardPanelSnapshot
    window: DashboardWindowSnapshot


def load_dashboard_snapshot(path: Path) -> DashboardSnapshot | None:
    if not path.exists():
        return None

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string(_read_dashboard_text(path))
    if not parser.sections():
        return None

    return DashboardSnapshot(
        broker_running=_read_bool(parser, "broker", "running"),
        controller_running=_read_bool(parser, "controller", "running"),
        f_mode_enabled=_read_bool(parser, "fmode", "enabled"),
        robot_link_enabled=_read_bool(parser, "robot_link", "enabled"),
        primary_uses_robot_hand=_read_bool(parser, "primary", "uses_robot_hand"),
        osr2_mode=parser.get("osr2", "mode", fallback="controlled"),
        mfp_connected=_read_bool(parser, "mfp", "connected"),
        primary=_read_panel(parser, "primary"),
        portrait=_read_panel(parser, "portrait"),
        landscape=_read_panel(parser, "landscape"),
        window=_read_window(parser),
    )


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
