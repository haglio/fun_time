"""Reading the panel's snapshot back off disk; the bridge beside it writes it."""
from __future__ import annotations

import configparser
from pathlib import Path

from .dashboard_bridge import DashboardSnapshot, decode_snapshot


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
        f_mode=_read_bool(parser, "fmode", "active"),
        in_vr=_read_bool(parser, "session", "vr"),
        nothing_to_reset=_read_bool(parser, "reset", "nothing"),
    )


def _read_bool(parser: configparser.ConfigParser, section: str, option: str) -> bool:
    return parser.get(section, option, fallback="0").strip() not in {"", "0", "false", "False"}
