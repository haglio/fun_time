"""The state file a session reads its own mode off.

One small INI in the state dir holds what the whole session is *in* — which
sides are locked, what each is filtered and ordered by, which players are in
F-mode, what is looping, where the sound is.  The dispatch loop owns it: it re-reads the
file every tick and writes it back after every command, so the state survives
its own resync and reaches the dashboard and both satellite HUDs, which are
separate processes drawing what the file says.

It lives here rather than beside the dispatch loop because startup writes it
too — seeding the session's opening mode before the loop exists (see
:func:`fun_time.session_resume.resume_shared_state`) — and the loop already
imports startup, so the file's shape has to sit under both.
"""
from __future__ import annotations

import configparser
from pathlib import Path

from .audio_volume import MAX_VOLUME
from .command_dispatch import BridgeState
from .mode_plan import STARTUP_MAIN_MODE

SHARED_STATE_FILENAME = "shared_bridge_state.ini"


def shared_state_path(state_dir: Path) -> Path:
    """Where *state_dir* keeps the shared state file."""
    return Path(state_dir) / SHARED_STATE_FILENAME


def write_shared_state(state_file: Path, state: BridgeState) -> None:
    """Write bridge state to the shared INI file the HUD and the guard read."""
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser["state"] = {
        "locked2": "1" if state.locked2 else "0",
        "locked3": "1" if state.locked3 else "0",
        "main_mode": state.main_mode,
        "main_f_mode": "1" if state.main_f_mode else "0",
        "portrait_f_mode": "1" if state.portrait_f_mode else "0",
        "landscape_f_mode": "1" if state.landscape_f_mode else "0",
        "omni_paused": "1" if state.omni_paused else "0",
        "active_side": str(state.active_side),
        "portrait_filter": state.portrait_filter,
        "landscape_filter": state.landscape_filter,
        "main_latest": "1" if state.main_latest else "0",
        "genau_latest": "1" if state.genau_latest else "0",
        "portrait_latest": "1" if state.portrait_latest else "0",
        "landscape_latest": "1" if state.landscape_latest else "0",
        "portrait_loop": state.portrait_loop,
        "landscape_loop": state.landscape_loop,
        "portrait_map_anchor": state.portrait_map_anchor,
        "landscape_map_anchor": state.landscape_map_anchor,
        "portrait_widen_clip": state.portrait_widen_clip,
        "landscape_widen_clip": state.landscape_widen_clip,
        "portrait_nav_anchor": state.portrait_nav_anchor,
        "landscape_nav_anchor": state.landscape_nav_anchor,
        "volume": str(state.volume),
        "muted": "1" if state.muted else "0",
    }
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fp:
        parser.write(fp)
    tmp.replace(state_file)


def _int_or(section, key: str, default: int) -> int:
    """An integer INI value, falling back to *default* when absent or malformed."""
    try:
        return int(section.get(key, default))
    except ValueError:
        return default


def read_shared_state(state_file: Path) -> BridgeState | None:
    """Read bridge state from the shared INI file."""
    if not state_file.exists():
        return None
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(state_file), encoding="utf-8")
    if "state" not in parser:
        return None
    s = parser["state"]
    return BridgeState(
        locked2=s.get("locked2", "0") == "1",
        locked3=s.get("locked3", "0") == "1",
        main_mode=s.get("main_mode", STARTUP_MAIN_MODE),
        main_f_mode=s.get("main_f_mode", "0") == "1",
        portrait_f_mode=s.get("portrait_f_mode", "0") == "1",
        landscape_f_mode=s.get("landscape_f_mode", "0") == "1",
        omni_paused=s.get("omni_paused", "0") == "1",
        active_side=_int_or(s, "active_side", 1),
        portrait_filter=s.get("portrait_filter", ""),
        landscape_filter=s.get("landscape_filter", ""),
        main_latest=s.get("main_latest", "0") == "1",
        genau_latest=s.get("genau_latest", "0") == "1",
        portrait_latest=s.get("portrait_latest", "0") == "1",
        landscape_latest=s.get("landscape_latest", "0") == "1",
        portrait_loop=s.get("portrait_loop", ""),
        landscape_loop=s.get("landscape_loop", ""),
        portrait_map_anchor=s.get("portrait_map_anchor", ""),
        landscape_map_anchor=s.get("landscape_map_anchor", ""),
        portrait_widen_clip=s.get("portrait_widen_clip", ""),
        landscape_widen_clip=s.get("landscape_widen_clip", ""),
        portrait_nav_anchor=s.get("portrait_nav_anchor", ""),
        landscape_nav_anchor=s.get("landscape_nav_anchor", ""),
        volume=_int_or(s, "volume", MAX_VOLUME),
        muted=s.get("muted", "0") == "1",
    )
