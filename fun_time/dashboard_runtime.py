"""The panel's own snapshot: what the bar draws.

The dispatch loop writes this INI every tick and the panel reads it back.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from .dashboard_bridge import decode_snapshot


@dataclass(frozen=True)
class DashboardSnapshot:
    omni_paused: bool
    voice_active: bool = True
    # The room's F-mode: every player narrowed at once.  Each player carries its
    # own switch on its own HUD, so the bar's one lights only when all three are
    # on — which is the only state a single button can honestly claim.
    f_mode: bool = False
    # Whether this session is the headset's.  The bar's last control is the way
    # across to the other one, and which way that is depends on where you are.
    in_vr: bool = False


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
    )


def _read_bool(parser: configparser.ConfigParser, section: str, option: str) -> bool:
    return parser.get(section, option, fallback="0").strip() not in {"", "0", "false", "False"}
