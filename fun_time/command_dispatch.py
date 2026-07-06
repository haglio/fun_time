"""Python-side command dispatcher for the Windows bridge.

Replaces the AHK ProcessDashboardCommand switch/case with a Python
implementation that calls logic modules directly as function calls
instead of subprocess invocations via the plan-file protocol.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from .media_actions import ensure_in_favs, make_web_url_from_path, move_to_weird, remove_from_favs
from .media_metadata import (
    GroupIndex,
    cached_group_index,
    load_metadata,
    metadata_path_for,
    normalize_path_key,
)
from .dashboard_runtime import genau_enabled_path, read_genau_enabled, read_nau_status
from .lock import build_lock_plan
from .modes import collect_video_files
from .provider_regen import regen_url_for_video
from .mode_plan import genau_active, nau_displays
from .runtime_flow import (
    apply_enter_omnipause,
    apply_leave_omnipause,
    apply_mode_switch,
    apply_refresh_recency_order,
    apply_toggle_fmode,
    build_omnipause_toggle,
)
from .vlc_actions import (
    ensure_playback_state,
    get_current_file_path,
    get_playlist_entries,
    set_repeat_mode,
    vlc_advance_and_remove,
    vlc_http_cmd,
    vlc_nav_step,
    vlc_play_playlist_item,
    vlc_swap_current_with,
)
from .watch_stats import record_watch_event, watch_stats_path

logger = logging.getLogger(__name__)


@dataclass
class BridgeState:
    locked2: bool = False
    locked3: bool = False
    primary_mode: str = "nau"
    f_mode_enabled: bool = False
    omni_paused: bool = False
    recency_order: bool = False


@dataclass
class BridgeConfig:
    portrait_port: int
    landscape_port: int
    vlc_password: str
    favs_file: Path
    weird_dir: Path
    state_dir: Path
    primary_sources: str
    portrait_sources: str
    landscape_sources: str
    genau_mode_file: Path
    genau_cmd_file: Path
    genau_paused_file: Path
    audio_paused_file: Path
    nau_cmd_file: Path
    nau_paused_file: Path
    nau_status_file: Path
    dashboard_state_file: Path
    broker_cmd_file: Path | None = None
    broker_heartbeat_file: Path | None = None
    broker_tray_launcher: Path | None = None
    provider_media_root: Path | None = None
    provider_metadata_root: Path | None = None
    provider_generate_video_url: str = "https://example.com/video"
    provider_generate_image_url: str = "https://example.com/create"


@dataclass(frozen=True)
class WindowOp:
    op: str
    pid: int = 0
    title: str = ""
    key: str = ""
    value: bool = True
    vk: int = 0
    exact: bool = False


_GENAU_CMD_MAP = {
    "genau_speed_down": "SPEED_DOWN",
    "genau_speed_up": "SPEED_UP",
    "genau_amplitude_down": "AMPLITUDE_DOWN",
    "genau_amplitude_up": "AMPLITUDE_UP",
    "genau_center_down": "CENTER_DOWN",
    "genau_center_up": "CENTER_UP",
    "genau_cycle_shape": "CYCLE_SHAPE",
    "genau_cycle_shape_prev": "CYCLE_SHAPE_PREV",
    "genau_toggle_cruise": "TOGGLE_CRUISE",
    "genau_cruise_on": "CRUISE_ON",
    "genau_cruise_off": "CRUISE_OFF",
    "genau_prev_clip": "PREV",
    "genau_next_clip": "NEXT",
}


_NAU_CMD_MAP = {
    "nau_record_down": "RECORD_DOWN",
    "nau_record_up": "RECORD_UP",
    "nau_record_tap": "RECORD_TAP",
    "nau_loop_cancel": "LOOP_CANCEL",
    "nau_cycle_version": "CYCLE_VERSION",
    "nau_toggle_length": "TOGGLE_LENGTH_MODE",
    "nau_length_shorts": "SET_LENGTH_MODE shorts",
    "nau_length_full": "SET_LENGTH_MODE full",
}


_GENAU_NUMERIC_PREFIXES = {
    "genau_amp_": "AMP",
    "genau_center_": "CENTER",
    "genau_speed_": "SPEED",
}


def _parse_genau_numeric_command(command: str) -> str | None:
    """Parse 'genau_amp_50' -> 'AMP 50', etc."""
    for prefix, keyword in _GENAU_NUMERIC_PREFIXES.items():
        if command.startswith(prefix):
            value_str = command[len(prefix):]
            try:
                int(value_str)
            except ValueError:
                return None
            return f"{keyword} {value_str}"
    return None


def _toggle_genau_enabled(path: Path) -> None:
    """Flip the persisted allow/suppress flag; the broker syncs it each tick."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("0" if read_genau_enabled(path) else "1", encoding="utf-8")


def _cancel_lock(which: int, state: BridgeState, config: BridgeConfig) -> BridgeState:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    plan = build_lock_plan("cancel-lock", which=which, locked=locked, current_path="")
    if plan.repeat_mode:
        set_repeat_mode(port, config.vlc_password, plan.repeat_mode)
    if which == 2:
        return replace(state, locked2=plan.next_locked)
    return replace(state, locked3=plan.next_locked)


def _toggle_lock(which: int, state: BridgeState, config: BridgeConfig) -> tuple[BridgeState, list[WindowOp]]:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    current_path = get_current_file_path(port, config.vlc_password)
    plan = build_lock_plan("toggle-lock", which=which, locked=locked, current_path=current_path)
    if plan.repeat_mode:
        set_repeat_mode(port, config.vlc_password, plan.repeat_mode)
    if plan.ensure_in_favs and current_path:
        ensure_in_favs(config.favs_file, current_path)
        # Locking is the strongest positive watch signal ("breeding" weight).
        record_watch_event(watch_stats_path(config.state_dir), current_path, "lock")
    if plan.advance_playlist:
        vlc_http_cmd(port, "pl_next", config.vlc_password)
    if plan.log_message:
        logger.info(plan.log_message)
    lock_ops: list[WindowOp] = []
    if plan.open_rfb_tab and current_path:
        url = regen_url_for_video(
            current_path,
            media_root=config.provider_media_root,
            metadata_root=config.provider_metadata_root,
            video_url=config.provider_generate_video_url,
            image_url=config.provider_generate_image_url,
        ) or make_web_url_from_path(current_path)
        if url:
            lock_ops.append(WindowOp(op="open_rfb_tab", key=url))
    if which == 2:
        return replace(state, locked2=plan.next_locked), lock_ops
    return replace(state, locked3=plan.next_locked), lock_ops


def _discard(which: int, state: BridgeState, config: BridgeConfig) -> BridgeState:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    current_path = get_current_file_path(port, config.vlc_password)
    plan = build_lock_plan("discard", which=which, locked=locked, current_path=current_path)
    if plan.repeat_mode:
        set_repeat_mode(port, config.vlc_password, plan.repeat_mode)
    if plan.remove_from_favs and current_path:
        remove_from_favs(config.favs_file, current_path)
    if plan.advance_playlist:
        vlc_advance_and_remove(port, config.vlc_password)
    if plan.move_to_weird and current_path:
        move_to_weird(config.weird_dir, Path(current_path))
    ensure_playback_state(port, config.vlc_password, should_play=True)
    if plan.log_message:
        logger.info(plan.log_message)
    if which == 2:
        return replace(state, locked2=plan.next_locked)
    return replace(state, locked3=plan.next_locked)


# display slot (2=portrait, 3=landscape) and variation axis per cycle command.
_CYCLE_COMMANDS = {
    "portrait_cycle_action": (2, "action"),
    "portrait_cycle_seed": (2, "seed"),
    "landscape_cycle_action": (3, "action"),
    "landscape_cycle_seed": (3, "seed"),
}


def _next_action_sibling(index: GroupIndex, current: str) -> str | None:
    """The action-group member after *current*, cycling in sorted order."""
    group_key = index.action_key_by_path.get(normalize_path_key(current))
    if group_key is None:
        return None
    members = [m for m in index.action_members[group_key] if Path(m).exists()]
    if len(members) < 2:
        return None
    current_key = normalize_path_key(current)
    for position, member in enumerate(members):
        if normalize_path_key(member) == current_key:
            return members[(position + 1) % len(members)]
    return members[0]


def _next_seed_sibling(
    index: GroupIndex, current: str, entries: list[tuple[int, str]]
) -> str | None:
    """A same-config different-seed video, preferring ones already in the playlist.

    Sisters are toured in seed order: the first seed above the current one,
    wrapping to the lowest, so repeated presses visit every subject.
    """
    seed_key = index.seed_key_by_path.get(normalize_path_key(current))
    if seed_key is None:
        return None
    family, current_seed = seed_key

    def sisters(paths) -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for path in paths:
            candidate = index.seed_key_by_path.get(normalize_path_key(path))
            if candidate and candidate[0] == family and candidate[1] != current_seed:
                found.append((candidate[1], path))
        return sorted(found)

    pool = sisters(path for _item_id, path in entries)
    if not pool:
        pool = sisters(m for m in index.seed_members.get(family, []) if Path(m).exists())
    if not pool:
        return None
    for seed, path in pool:
        if seed > current_seed:
            return path
    return pool[0][1]


def _video_action_label(video_path: str, config: BridgeConfig) -> str:
    meta_path = metadata_path_for(video_path, config.provider_media_root, config.provider_metadata_root)
    if meta_path is None or not meta_path.is_file():
        return ""
    video = load_metadata(meta_path).get("video") or {}
    return str(video.get("action") or "").strip()


def _cycle_variant(
    which: int, kind: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Switch the satellite's current video to a sibling: another action of the
    same subject(s)+situation, or the same configuration under another seed."""
    port = config.portrait_port if which == 2 else config.landscape_port
    sources = config.portrait_sources if which == 2 else config.landscape_sources
    ops: list[WindowOp] = []
    state = _cancel_lock(which, state, config)
    current = get_current_file_path(port, config.vlc_password)
    if not current:
        return state, ops
    index = cached_group_index(
        sources,
        paths_supplier=lambda: collect_video_files(sources),
        media_root=config.provider_media_root,
        metadata_root=config.provider_metadata_root,
        must_contain=current,
    )
    entries, _current_id = get_playlist_entries(port, config.vlc_password)
    if kind == "action":
        target = _next_action_sibling(index, current)
        missing_message = "No other actions"
    else:
        target = _next_seed_sibling(index, current, entries)
        missing_message = "No other seeds"
    if target is None:
        ops.append(WindowOp(op="tooltip", key=missing_message))
        return state, ops
    entry_ids = {normalize_path_key(path): item_id for item_id, path in entries}
    target_id = entry_ids.get(normalize_path_key(target))
    if target_id is not None:
        switched = vlc_play_playlist_item(port, config.vlc_password, target_id)
    else:
        switched = vlc_swap_current_with(port, config.vlc_password, target)
    if not switched:
        logger.warning("cycle %s: could not switch to %s", kind, target)
        return state, ops
    ensure_playback_state(port, config.vlc_password, should_play=True)
    if kind == "action":
        action = _video_action_label(target, config)
        if action:
            ops.append(WindowOp(op="tooltip", key=f"Action: {action}"))
    return state, ops


def dispatch_command(
    command: str,
    state: BridgeState,
    config: BridgeConfig,
) -> tuple[BridgeState, list[WindowOp]]:
    """Dispatch a dashboard/hotkey command, returning updated state and window operations."""
    ops: list[WindowOp] = []

    cycle_target = _CYCLE_COMMANDS.get(command)
    if cycle_target is not None:
        which, kind = cycle_target
        return _cycle_variant(which, kind, state, config)

    if command == "portrait_prev":
        state = _cancel_lock(2, state, config)
        vlc_nav_step(config.portrait_port, config.vlc_password, "prev")
        ensure_playback_state(config.portrait_port, config.vlc_password, should_play=True)
        return state, ops

    if command == "portrait_next":
        state = _cancel_lock(2, state, config)
        vlc_nav_step(config.portrait_port, config.vlc_password, "next")
        ensure_playback_state(config.portrait_port, config.vlc_password, should_play=True)
        return state, ops

    if command == "portrait_lock":
        state, lock_ops = _toggle_lock(2, state, config)
        ops.extend(lock_ops)
        return state, ops

    if command == "portrait_trash":
        state = _discard(2, state, config)
        return state, ops

    if command == "landscape_prev":
        state = _cancel_lock(3, state, config)
        vlc_nav_step(config.landscape_port, config.vlc_password, "prev")
        ensure_playback_state(config.landscape_port, config.vlc_password, should_play=True)
        return state, ops

    if command == "landscape_next":
        state = _cancel_lock(3, state, config)
        vlc_nav_step(config.landscape_port, config.vlc_password, "next")
        ensure_playback_state(config.landscape_port, config.vlc_password, should_play=True)
        return state, ops

    if command == "landscape_lock":
        state, lock_ops = _toggle_lock(3, state, config)
        ops.extend(lock_ops)
        return state, ops

    if command == "landscape_trash":
        state = _discard(3, state, config)
        return state, ops

    if command in ("primary_prev", "primary_next"):
        # Nau owns the primary display in nau and hybrid; in genau mode the
        # paused Nau still navigates in the background.
        config.nau_cmd_file.write_text(
            "PREV" if command == "primary_prev" else "NEXT", encoding="utf-8",
        )
        return state, ops

    if command in ("primary_nudge_prev", "primary_nudge_next"):
        # Nau owns the primary display; its SEEK commands apply to a live local
        # clock, so rapid nudges stack naturally.
        config.nau_cmd_file.write_text(
            "SEEK_BACK" if command == "primary_nudge_prev" else "SEEK_FWD",
            encoding="utf-8",
        )
        return state, ops

    if command in _NAU_CMD_MAP:
        # Loop recording, versions and length only make sense while Nau owns the
        # primary display — nau and hybrid, but not genau.
        if nau_displays(state.primary_mode):
            config.nau_cmd_file.write_text(_NAU_CMD_MAP[command], encoding="utf-8")
        return state, ops

    if command == "quarter_button":
        config.genau_cmd_file.write_text("OFFSET_QUARTER_CYCLE", encoding="utf-8")
        return state, ops

    if command == "omnipause_toggle":
        return _dispatch_omnipause_toggle(state, config)

    if command == "enter_omnipause":
        return _dispatch_enter_omnipause(state, config)

    if command == "leave_omnipause":
        return _dispatch_leave_omnipause(state, config)

    if command in ("fmode_toggle", "fmode_panel"):
        return _dispatch_fmode_toggle(state, config)

    if command == "recency_order_refresh":
        return _dispatch_recency_order_refresh(state, config)

    if command in ("genau_activate", "nau_activate", "hybrid_activate"):
        target = {"genau_activate": "genau", "nau_activate": "nau", "hybrid_activate": "hybrid"}[command]
        return _dispatch_mode_switch(target, state, config, ops)

    if command == "genau_toggle_auto":
        # Flip whether Genau may take over while OSR2 is in auto mode. The broker
        # reads this persisted flag each tick, so a plain file write is enough.
        _toggle_genau_enabled(genau_enabled_path(config.state_dir))
        return state, ops

    if command in _GENAU_CMD_MAP:
        if genau_active(state.primary_mode):
            config.genau_cmd_file.write_text(_GENAU_CMD_MAP[command], encoding="utf-8")
        return state, ops

    genau_numeric = _parse_genau_numeric_command(command)
    if genau_numeric is not None:
        if genau_active(state.primary_mode):
            config.genau_cmd_file.write_text(genau_numeric, encoding="utf-8")
        return state, ops

    if command == "clipper_save":
        if state.primary_mode != "genau":
            msg = _dispatch_clipper_save(config)
            if msg:
                ops.append(WindowOp(op="tooltip", key=msg))
        return state, ops

    return state, ops


def _dispatch_omnipause_toggle(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    ops: list[WindowOp] = []
    toggle = build_omnipause_toggle(
        omni_paused=state.omni_paused,
        primary_mode=state.primary_mode,
    )
    if toggle.action == "enter":
        result = apply_enter_omnipause(
            omni_paused=state.omni_paused,
            primary_mode=state.primary_mode,
            portrait_port=config.portrait_port,
            landscape_port=config.landscape_port,
            password=config.vlc_password,
            genau_paused_file=config.genau_paused_file,
            audio_paused_file=config.audio_paused_file,
            genau_cmd_file=config.genau_cmd_file,
            nau_paused_file=config.nau_paused_file,
            broker_cmd_file=config.broker_cmd_file,
        )
        state = replace(state, omni_paused=result.next_omni_paused)
        ops.append(WindowOp(op="disable_all_topmost"))
        ops.append(WindowOp(op="suspend_hotkeys"))
    else:
        result = apply_leave_omnipause(
            omni_paused=state.omni_paused,
            primary_mode=state.primary_mode,
            portrait_port=config.portrait_port,
            landscape_port=config.landscape_port,
            password=config.vlc_password,
            genau_paused_file=config.genau_paused_file,
            audio_paused_file=config.audio_paused_file,
            genau_cmd_file=config.genau_cmd_file,
            nau_paused_file=config.nau_paused_file,
            broker_cmd_file=config.broker_cmd_file,
        )
        state = replace(state, omni_paused=result.next_omni_paused)
        ops.append(WindowOp(op="restore_all_topmost"))
        ops.append(WindowOp(op="unsuspend_hotkeys"))
        ops.extend(_primary_focus_ops(state.primary_mode))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _dispatch_enter_omnipause(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    ops: list[WindowOp] = []
    result = apply_enter_omnipause(
        omni_paused=state.omni_paused,
        primary_mode=state.primary_mode,
        portrait_port=config.portrait_port,
        landscape_port=config.landscape_port,
        password=config.vlc_password,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        nau_paused_file=config.nau_paused_file,
        broker_cmd_file=config.broker_cmd_file,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    ops.append(WindowOp(op="disable_all_topmost"))
    ops.append(WindowOp(op="suspend_hotkeys"))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _dispatch_leave_omnipause(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    ops: list[WindowOp] = []
    result = apply_leave_omnipause(
        omni_paused=state.omni_paused,
        primary_mode=state.primary_mode,
        portrait_port=config.portrait_port,
        landscape_port=config.landscape_port,
        password=config.vlc_password,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        nau_paused_file=config.nau_paused_file,
        broker_cmd_file=config.broker_cmd_file,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    ops.append(WindowOp(op="restore_all_topmost"))
    ops.append(WindowOp(op="unsuspend_hotkeys"))
    ops.extend(_primary_focus_ops(state.primary_mode))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _primary_focus_ops(primary_mode: str) -> list[WindowOp]:
    """Re-activate the window that owns the primary display (omnipause leave)."""
    role = "genau" if genau_active(primary_mode) else "nau"
    return [WindowOp(op="activate_role", key=role)]


def _primary_slot_ops(primary_mode: str) -> list[WindowOp]:
    """Visibility ops for the primary-slot windows on a mode switch.

    The two players (Nau and Genau) share one screen rect; exactly the mode's
    player(s) are shown and the inactive slot-mate hidden.  The new window is
    shown and activated BEFORE the old one hides so focus never falls through
    to another application.
    """
    if primary_mode == "genau":
        return [
            WindowOp(op="show_role", key="genau"),
            WindowOp(op="activate_role", key="genau"),
            WindowOp(op="hide_role", key="nau"),
        ]
    if primary_mode == "hybrid":
        return [
            WindowOp(op="show_role", key="nau"),
            WindowOp(op="show_role", key="genau"),
            WindowOp(op="activate_role", key="genau"),
        ]
    return [
        WindowOp(op="show_role", key="nau"),
        WindowOp(op="activate_role", key="nau"),
        WindowOp(op="hide_role", key="genau"),
    ]


def _dispatch_fmode_toggle(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_toggle_fmode(
        f_mode_enabled=state.f_mode_enabled,
        recent=state.recency_order,
        primary_sources=config.primary_sources,
        portrait_sources=config.portrait_sources,
        landscape_sources=config.landscape_sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        portrait_port=config.portrait_port,
        landscape_port=config.landscape_port,
        password=config.vlc_password,
        nau_cmd_file=config.nau_cmd_file,
        provider_media_root=config.provider_media_root,
        provider_metadata_root=config.provider_metadata_root,
    )
    if result.log_message:
        logger.info(result.log_message)
    return replace(
        state,
        f_mode_enabled=result.next_f_mode_enabled,
        locked2=result.next_locked2,
        locked3=result.next_locked3,
    ), []


def _dispatch_recency_order_refresh(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_refresh_recency_order(
        f_mode_enabled=state.f_mode_enabled,
        portrait_sources=config.portrait_sources,
        landscape_sources=config.landscape_sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        portrait_port=config.portrait_port,
        landscape_port=config.landscape_port,
        password=config.vlc_password,
    )
    if result.log_message:
        logger.info(result.log_message)
    return replace(
        state,
        recency_order=result.next_recency_order,
        locked2=result.next_locked2,
        locked3=result.next_locked3,
    ), []


def _dispatch_mode_switch(
    target_mode: str, state: BridgeState, config: BridgeConfig, ops: list[WindowOp]
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_mode_switch(
        current_mode=state.primary_mode,
        target_mode=target_mode,
        omni_paused=state.omni_paused,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        nau_paused_file=config.nau_paused_file,
        nau_cmd_file=config.nau_cmd_file,
        broker_cmd_file=config.broker_cmd_file,
    )
    state = replace(state, primary_mode=result.next_mode)
    if result.is_transition:
        ops.extend(_primary_slot_ops(result.next_mode))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


_CLIPPER_PROJECT_DIR = Path(__file__).resolve().parents[1].parent / "clipper"
_CLIPPER_PYTHON = _CLIPPER_PROJECT_DIR / ".venv" / "Scripts" / "python.exe"


def _clipper_python() -> str:
    if _CLIPPER_PYTHON.is_file():
        return str(_CLIPPER_PYTHON)
    return sys.executable


def _current_primary_media(config: BridgeConfig) -> tuple[str, float]:
    """The primary display's current video path and playback time (seconds).

    Nau owns the primary display in every mode it appears (nau and hybrid) and
    publishes both in its status file; the path is empty when nothing is playing.
    """
    status = read_nau_status(config.nau_status_file)
    return status.video, status.position_ms / 1000


def _dispatch_clipper_save(config: BridgeConfig) -> str:
    """Save a Clipper session for the primary display's current video.

    Returns a short user-visible message on success, or empty string on failure.
    """
    video_path, playback_time = _current_primary_media(config)
    if not video_path:
        logger.warning("clipper_save: no video playing on the primary display")
        return ""
    try:
        result = subprocess.run(
            [
                _clipper_python(), "-m", "clipper.create_session",
                "--video", video_path,
                "--time", str(playback_time),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(_CLIPPER_PROJECT_DIR),
        )
        if result.returncode == 0:
            session_path = result.stdout.strip()
            logger.info("clipper_save: %s", session_path)
            name = Path(session_path).stem if session_path else "session"
            return f"Clipper: {name}"
        logger.warning("clipper_save failed: %s", result.stderr.strip())
        return ""
    except Exception as exc:
        logger.warning("clipper_save error: %s", exc)
        return ""
