from __future__ import annotations

import configparser
import time
from dataclasses import dataclass
from pathlib import Path


def genau_enabled_path(state_dir: Path) -> Path:
    """Path to the broker-shared flag for whether Genau may take over OSR2 auto mode."""
    return state_dir / "genau_enabled.txt"


def read_genau_enabled(path: Path) -> bool:
    """True (takeover allowed) unless the flag file holds '0' — mirrors the broker."""
    try:
        if not path.exists():
            return True
        return path.read_text(encoding="utf-8-sig").strip() != "0"
    except OSError:
        return True


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
    primary_mode: str
    osr2_mode: str
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
        primary_mode=parser.get("primary", "mode", fallback="nau"),
        osr2_mode=parser.get("osr2", "mode", fallback="controlled"),
        primary_responsive=_read_bool(parser, "primary", "responsive"),
        omni_paused=_read_bool(parser, "omnipause", "active"),
        voice_active=_read_bool(parser, "voice", "active") if parser.has_section("voice") else True,
        primary=_read_panel(parser, "primary"),
        portrait=_read_panel(parser, "portrait"),
        landscape=_read_panel(parser, "landscape"),
        window=_read_window(parser),
    )


@dataclass(frozen=True)
class NauStatus:
    """Snapshot of what Nau is playing, parsed from its status file.

    Only the fields with consumers on this side are parsed.  ``position_ms``
    and ``duration_ms`` give the playback fraction watch tracking needs; the
    hybrid handoff arbiter drives the OSR2 from the funscript while
    ``has_funscript`` and not ``funscript_resting``, and hands off to Genau
    otherwise — so Genau fills a funscript's quiet lead-in and interior gaps
    (where ``funscript_resting`` is set).
    """

    video: str = ""
    position_ms: int = 0
    duration_ms: int = 0
    state: str = "normal"
    paused: bool = False
    has_funscript: bool = False
    funscript_resting: bool = False

    @property
    def funscript_driving(self) -> bool:
        """True when the funscript is actively driving the OSR2 — scripted and
        not resting.  The moment-to-moment hybrid handoff signal: whoever this
        points to (Nau's funscript, else Genau) also owns speed control."""
        return self.has_funscript and not self.funscript_resting


def read_nau_status(path: Path) -> NauStatus:
    if not path.exists():
        return NauStatus()
    try:
        text = path.read_text(encoding="utf-8")
        values = dict(
            line.split("=", 1) for line in text.splitlines() if "=" in line
        )
        return NauStatus(
            video=values.get("video", "").strip(),
            position_ms=int(values.get("position_ms", "0").strip() or 0),
            duration_ms=int(values.get("duration_ms", "0").strip() or 0),
            state=values.get("state", "normal").strip(),
            paused=_status_bool(values, "paused"),
            has_funscript=_status_bool(values, "has_funscript"),
            funscript_resting=_status_bool(values, "funscript_resting"),
        )
    except (OSError, ValueError):
        return NauStatus()


@dataclass(frozen=True)
class GenauStatus:
    cruise_active: bool = False
    shape: str = "sine"
    amp_at_max: bool = False
    amp_at_min: bool = False
    ctr_at_max: bool = False
    ctr_at_min: bool = False
    spd_at_max: bool = False
    spd_at_min: bool = False


def _status_bool(values: dict[str, str], key: str) -> bool:
    return values.get(key, "0").strip() not in ("0", "false", "")


def read_genau_status(path: Path) -> GenauStatus:
    if not path.exists():
        return GenauStatus()
    try:
        text = path.read_text(encoding="utf-8").strip()
        values = dict(
            line.split("=", 1) for line in text.splitlines() if "=" in line
        )
        return GenauStatus(
            cruise_active=_status_bool(values, "cruise"),
            shape=values.get("shape", "sine").strip(),
            amp_at_max=_status_bool(values, "amp_at_max"),
            amp_at_min=_status_bool(values, "amp_at_min"),
            ctr_at_max=_status_bool(values, "ctr_at_max"),
            ctr_at_min=_status_bool(values, "ctr_at_min"),
            spd_at_max=_status_bool(values, "spd_at_max"),
            spd_at_min=_status_bool(values, "spd_at_min"),
        )
    except (OSError, ValueError):
        return GenauStatus()


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
