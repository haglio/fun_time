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
    main_mode: str
    osr2_mode: str
    omni_paused: bool
    main: DashboardPanelSnapshot
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
        main_mode=parser.get("main", "mode", fallback="nau"),
        osr2_mode=parser.get("osr2", "mode", fallback="controlled"),
        omni_paused=_read_bool(parser, "omnipause", "active"),
        voice_active=_read_bool(parser, "voice", "active") if parser.has_section("voice") else True,
        main=_read_panel(parser, "main"),
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
    # Whether Nau is holding the video on screen rather than letting it end.  The
    # main console draws the lock, and in genau mode it is drawn by a player
    # with no such lock of its own to ask — so it comes through here, the way the
    # loop ``state`` does.  Defaults on because that is what a main player with
    # nothing to say is doing.
    locked: bool = True
    # The A/B range Nau is looping, as it published it — 0/0 when nothing is.
    # Read through :attr:`loop_bounds` rather than directly; the pair only means
    # a loop alongside ``state``.
    loop_in_ms: int = 0
    loop_out_ms: int = 0
    # The touch-down Nau's trace chose for the handoff boundary in play, in
    # media ms — the arbiter ends Genau's turn there, so the device is set down
    # exactly where the picture drew the blue ending.  None when there is no
    # chosen touch (a raised floor takes the ramp and flips at once).
    handoff_touch_ms: int | None = None

    @property
    def funscript_driving(self) -> bool:
        """True when the funscript is actively driving the OSR2 — scripted and
        not resting.  The moment-to-moment hybrid handoff signal: whoever this
        points to (Nau's funscript, else Genau) also takes the unqualified speed
        nudge, since that is the engine a nudge can actually move."""
        return self.has_funscript and not self.funscript_resting

    @property
    def loop_bounds(self) -> tuple[int, int] | None:
        """The loop Nau is running, or None for no loop.

        A loop dies with the player process holding it, so this file is its only
        record and a reopened session is handed it back over the video the resume
        put at the top of the main player's playlist.  Both halves have to agree: a
        state of "looping" with no range is a Nau too old to publish one, and a
        range with nothing looping is the empty pair a cancelled loop leaves —
        either taken alone would hand mpv a loop it cannot play.
        """
        if self.state != "looping" or self.loop_out_ms <= self.loop_in_ms:
            return None
        return (self.loop_in_ms, self.loop_out_ms)


def read_nau_status(path: Path, *, fallback: NauStatus | None = None) -> NauStatus:
    """Nau's published status, or *fallback* (else a default) when the file is
    missing or torn mid-replace.

    The fallback matters to the hybrid arbiter: a default snapshot reads
    ``funscript_driving`` False, so one torn read mid-cluster flipped the
    device to Genau and the next good read flipped it straight back — a
    spurious double handoff nobody asked for.  Handing back the last good
    snapshot makes a failed read a non-event.
    """
    if not path.exists():
        return fallback or NauStatus()
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
            locked=_status_bool(values, "locked", default=True),
            loop_in_ms=int(values.get("loop_in_ms", "0").strip() or 0),
            loop_out_ms=int(values.get("loop_out_ms", "0").strip() or 0),
            handoff_touch_ms=_status_touch(values),
        )
    except (OSError, ValueError):
        return fallback or NauStatus()


def _status_touch(values: dict) -> int | None:
    """The touch-down Nau's trace chose for the boundary in play, or None —
    absent on a raised floor, an unlatched forecast, or an older Nau."""
    raw = values.get("handoff_touch_ms", "").strip()
    return int(raw) if raw.isdigit() else None


GENAU_STATUS_FILENAME = "genau_status.txt"


def genau_status_path(state_dir: Path) -> Path:
    """Where Genau publishes what it is doing, in *state_dir*."""
    return Path(state_dir) / GENAU_STATUS_FILENAME


@dataclass(frozen=True)
class GenauStatus:
    cruise_active: bool = False
    # Whether Genau is holding the clip on screen rather than letting its interval
    # carry it on — the same lock Nau has, and on for the same reason: a clip
    # repeating is where Genau opens.  Cruise is a separate thing entirely; it
    # varies the stroke, never which clip plays.
    locked: bool = True
    shape: str = "sine"
    # The clip on screen, as Genau published it — "" before the first one is up,
    # and from a Genau too old to say.  Genau rescans its folder every launch and
    # opens at the top of it, so this is the only record of where a session was.
    clip: str = ""


def _status_bool(values: dict[str, str], key: str, *, default: bool = False) -> bool:
    """*key* read as a flag, or *default* where the file does not carry it.

    A default of True is for a flag whose "on" is the player's own resting state:
    a status that predates the key, or one read before the player's first write,
    then says what the player is actually doing rather than the opposite.
    """
    return values.get(key, "1" if default else "0").strip() not in ("0", "false", "")


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
            locked=_status_bool(values, "locked", default=True),
            shape=values.get("shape", "sine").strip(),
            clip=values.get("clip", "").strip(),
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
