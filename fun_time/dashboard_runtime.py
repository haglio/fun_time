from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DashboardPanelSnapshot:
    label: str
    clip: str
    highlight: bool
    accent: str = ""


@dataclass(frozen=True)
class DashboardSnapshot:
    broker_running: bool
    controller_running: bool
    f_mode_enabled: bool
    robot_link_enabled: bool
    osr2_mode: str
    mfp_connected: bool
    primary: DashboardPanelSnapshot
    portrait: DashboardPanelSnapshot
    landscape: DashboardPanelSnapshot


def load_dashboard_snapshot(path: Path) -> DashboardSnapshot | None:
    if not path.exists():
        return None

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    if not parser.sections():
        return None

    return DashboardSnapshot(
        broker_running=_read_bool(parser, "broker", "running"),
        controller_running=_read_bool(parser, "controller", "running"),
        f_mode_enabled=_read_bool(parser, "fmode", "enabled"),
        robot_link_enabled=_read_bool(parser, "robot_link", "enabled"),
        osr2_mode=parser.get("osr2", "mode", fallback="controlled"),
        mfp_connected=_read_bool(parser, "mfp", "connected"),
        primary=_read_panel(parser, "primary"),
        portrait=_read_panel(parser, "portrait"),
        landscape=_read_panel(parser, "landscape"),
    )


def _read_bool(parser: configparser.ConfigParser, section: str, option: str) -> bool:
    return parser.get(section, option, fallback="0").strip() not in {"", "0", "false", "False"}


def _read_panel(parser: configparser.ConfigParser, section: str) -> DashboardPanelSnapshot:
    return DashboardPanelSnapshot(
        label=parser.get(section, "label", fallback=""),
        clip=parser.get(section, "clip", fallback=""),
        highlight=_read_bool(parser, section, "highlight"),
        accent=parser.get(section, "accent", fallback=""),
    )
