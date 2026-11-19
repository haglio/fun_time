from __future__ import annotations

import configparser
from pathlib import Path

from .dashboard_bridge import (
    SNAPSHOT_WORD_PREFIX,
    DashboardSnapshot,
    decode_snapshot,
    is_snapshot_word,
)
from .rfb_modes import RfbMode


def load_dashboard_snapshot(path: Path) -> DashboardSnapshot | None:
    if not path.exists():
        return None
    return parse_dashboard_snapshot(decode_snapshot(path.read_bytes()))


def snapshot_in(word: str) -> DashboardSnapshot | None:
    if not is_snapshot_word(word):
        return None
    return parse_dashboard_snapshot(word.removeprefix(SNAPSHOT_WORD_PREFIX))


def parse_dashboard_snapshot(text: str) -> DashboardSnapshot | None:
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read_string(text)
    if not parser.sections():
        return None

    return DashboardSnapshot(
        omni_paused=_read_bool(parser, "omnipause", "active"),
        voice_active=_read_bool(parser, "voice", "active") if parser.has_section("voice") else True,
        favorites=_read_bool(parser, "favorites", "active"),
        in_vr=_read_bool(parser, "session", "vr"),
        nothing_to_reset=_read_bool(parser, "reset", "nothing"),
        headset_ready=(not parser.has_section("headset")
                       or _read_bool(parser, "headset", "ready")),
        origenerator_core_ready=_read_bool(parser, "origenerator", "core_ready"),
        rfb_mode=(RfbMode.ORIGENERATOR if _read_bool(parser, "origenerator", "in_the_rfb_mode")
                  else RfbMode.RFB),
    )


def _read_bool(parser: configparser.ConfigParser, section: str, option: str) -> bool:
    return parser.get(section, option, fallback="0").strip() not in {"", "0", "false", "False"}
