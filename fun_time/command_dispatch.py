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
    action_group_members,
    action_label,
    cached_group_index,
    load_metadata,
    metadata_path_for,
    normalize_path_key,
    seed_family_members,
)
from .dashboard_runtime import genau_enabled_path, read_genau_enabled, read_nau_status
from .lock import build_lock_plan
from .modes import collect_video_files
from .provider_regen import regen_url_for_video
from .mode_plan import genau_active, nau_displays
from .filter_vocab import decode_filter_command
from .runtime_flow import (
    apply_enter_omnipause,
    apply_leave_omnipause,
    apply_mode_switch,
    apply_refresh_recency_order,
    apply_satellite_filter,
    apply_satellite_loop,
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
from .event_log import (
    SOURCE_LANDSCAPE,
    SOURCE_PORTRAIT,
    SOURCE_PRIMARY,
    SOURCE_SYSTEM,
)

logger = logging.getLogger(__name__)


def _satellite_source(which: int) -> str:
    """The event-log source for satellite slot *which* (2=portrait, 3=landscape)."""
    return SOURCE_PORTRAIT if which == 2 else SOURCE_LANDSCAPE


@dataclass
class BridgeState:
    locked2: bool = False
    locked3: bool = False
    primary_mode: str = "nau"
    f_mode_enabled: bool = False
    omni_paused: bool = False
    recency_order: bool = False
    # The player most recently navigated (1=primary/Nau, 2=portrait,
    # 3=landscape). Any portrait_/landscape_ command, or a primary next/prev,
    # updates it; the side-agnostic "active_*" commands resolve against it —
    # nav (next/prev) reaches all three, the satellite-only actions only 2/3.
    active_side: int = 2
    # Per-VLC metadata filter queries ("" = no filter). Persisted in the shared
    # state file so they survive the dispatch loop's per-tick state resync and
    # are honoured by later F-mode / premiere rebuilds.
    portrait_filter: str = ""
    landscape_filter: str = ""


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
    # Which window a ``notice`` op is about, so the log panel can filter it.
    source: str = SOURCE_SYSTEM


_GENAU_CMD_MAP = {
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


# Speed control splits by kind.  A relative nudge (up/down) follows whichever
# engine currently drives the OSR2 — Genau's stroke rate, or Nau's video (whose
# mpv clock scales the funscript with it).  An absolute video-speed set (min /
# max / a spoken multiplier) tunes whatever Nau is showing — the video on
# screen — so it lands even during a Genau-driven stretch; Genau has no
# multiplier, so that side is a no-op there.
_SPEED_RELATIVE = {
    "genau_speed_down": "SPEED_DOWN",
    "genau_speed_up": "SPEED_UP",
}
_SPEED_EXTREMES = {
    # command -> (nau command, genau command)
    "speed_min": ("SET_SPEED min", "SPEED 0"),
    "speed_max": ("SET_SPEED max", "SPEED 100"),
}


def _parse_nau_speed(command: str) -> str | None:
    """'nau_speed_150' -> 'SET_SPEED 1.5' (percent-of-normal -> multiplier)."""
    prefix = "nau_speed_"
    if not command.startswith(prefix):
        return None
    try:
        pct = int(command[len(prefix):])
    except ValueError:
        return None
    return f"SET_SPEED {pct / 100:g}"


def _speed_engine_commands(command: str) -> tuple[str | None, str | None, bool] | None:
    """Map a speed command to (nau_cmd, genau_cmd, per_stretch), or None.

    ``per_stretch`` True marks a relative nudge that follows the OSR2 driver;
    False marks an absolute video-speed set that tunes whatever Nau shows.  A
    ``None`` side means that engine has no equivalent and ignores the command.
    """
    if command in _SPEED_RELATIVE:
        keyword = _SPEED_RELATIVE[command]
        return keyword, keyword, True
    if command in _SPEED_EXTREMES:
        nau_cmd, genau_cmd = _SPEED_EXTREMES[command]
        return nau_cmd, genau_cmd, False
    nau_cmd = _parse_nau_speed(command)
    if nau_cmd is not None:
        return nau_cmd, None, False
    return None


def _speed_target(state: BridgeState, config: BridgeConfig, *, per_stretch: bool) -> str:
    """Which engine a speed command drives.

    genau mode -> 'genau'; nau mode -> 'nau'.  In hybrid a relative nudge
    (``per_stretch``) follows the current OSR2 driver — Nau's funscript while it
    is active, else Genau — while an absolute video-speed set tunes Nau's video,
    the thing on screen, no matter which is driving.
    """
    if not genau_active(state.primary_mode):
        return "nau"
    if not nau_displays(state.primary_mode):
        return "genau"
    if not per_stretch:
        return "nau"
    return "nau" if read_nau_status(config.nau_status_file).funscript_driving else "genau"


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


def _same_video(left: str, right: str) -> bool:
    return normalize_path_key(left) == normalize_path_key(right)


def _play_video(port: int, password: str, path: str, entries: list[tuple[int, str]]) -> bool:
    """Make *path* the satellite's current item, playing it from *entries* when
    it is already queued and swapping it in over the current item otherwise."""
    entry_ids = {normalize_path_key(entry_path): item_id for item_id, entry_path in entries}
    target_id = entry_ids.get(normalize_path_key(path))
    if target_id is not None:
        return vlc_play_playlist_item(port, password, target_id)
    return vlc_swap_current_with(port, password, path)


def _cancel_lock(which: int, state: BridgeState, config: BridgeConfig) -> BridgeState:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    plan = build_lock_plan("cancel-lock", which=which, locked=locked, current_path="")
    if plan.repeat_mode:
        set_repeat_mode(port, config.vlc_password, plan.repeat_mode)
    if which == 2:
        return replace(state, locked2=plan.next_locked)
    return replace(state, locked3=plan.next_locked)


def _toggle_lock(
    which: int, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    current_path = get_current_file_path(port, config.vlc_password)
    # "Lock" names the video the speaker had in front of them.  If the satellite
    # auto-advanced while the phrase was being recognized, bring that video back
    # and lock it — the whole point of the lock is to keep watching *it*.  An
    # unlock needs no such rescue: a locked satellite repeats one video and
    # cannot have advanced.
    if not locked and target_path and not _same_video(target_path, current_path):
        entries, _current_id = get_playlist_entries(port, config.vlc_password)
        if _play_video(port, config.vlc_password, target_path, entries):
            logger.info("Lock back-dated to %s (player %d had advanced)", target_path, which)
            current_path = target_path
        else:
            logger.warning("Lock could not return player %d to %s", which, target_path)
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


def _drop_playlist_entry(port: int, password: str, path: str) -> bool:
    """Delete *path* from the playlist wherever it sits, leaving playback alone."""
    entries, _current_id = get_playlist_entries(port, password)
    for item_id, entry_path in entries:
        if _same_video(entry_path, path):
            return vlc_http_cmd(port, f"pl_delete&id={item_id}", password)
    return False


def _discard(
    which: int, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> BridgeState:
    port = config.portrait_port if which == 2 else config.landscape_port
    locked = state.locked2 if which == 2 else state.locked3
    current_path = get_current_file_path(port, config.vlc_password)
    # "Weird" condemns the video the speaker saw.  When the satellite advanced
    # while the phrase was being recognized there is nothing to advance past —
    # the condemned video is dropped from the playlist where it now sits, and
    # the innocent video that replaced it keeps playing.
    condemned = target_path or current_path
    already_moved_on = bool(target_path) and not _same_video(target_path, current_path)
    plan = build_lock_plan("discard", which=which, locked=locked, current_path=condemned)
    if plan.repeat_mode:
        set_repeat_mode(port, config.vlc_password, plan.repeat_mode)
    if plan.remove_from_favs and condemned:
        remove_from_favs(config.favs_file, condemned)
    if plan.advance_playlist:
        if already_moved_on:
            _drop_playlist_entry(port, config.vlc_password, condemned)
        else:
            vlc_advance_and_remove(port, config.vlc_password)
    if plan.move_to_weird and condemned:
        move_to_weird(config.weird_dir, Path(condemned))
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
) -> tuple[str | None, bool]:
    """The next seed sibling of *current* and whether the net had to widen.

    First choice is an exact same-config sister (a different seed of the
    identical config). When none exists, the net widens to the same *scene*
    with the render knobs freed, so a near-match still comes up instead of a
    dead end. Either pool is toured in seed order — the first seed above the
    current one, wrapping to the lowest — preferring playlist entries.
    """
    current_key = normalize_path_key(current)

    def pool(key_by_path, members_by_family, accept) -> tuple[str | None, list[tuple[str, str]]]:
        entry = key_by_path.get(current_key)
        if entry is None:
            return None, []
        family, current_seed = entry

        def gather(paths) -> list[tuple[str, str]]:
            found: list[tuple[str, str]] = []
            for path in paths:
                candidate = key_by_path.get(normalize_path_key(path))
                if candidate and candidate[0] == family and accept(candidate[1], path, current_seed):
                    found.append((candidate[1], path))
            return sorted(found)

        found = gather(path for _item_id, path in entries)
        if not found:
            found = gather(m for m in members_by_family.get(family, []) if Path(m).exists())
        return current_seed, found

    current_seed, candidates = pool(
        index.seed_key_by_path, index.seed_members,
        lambda seed, _path, cur: seed != cur,
    )
    widened = False
    if not candidates:
        current_seed, candidates = pool(
            index.loose_seed_key_by_path, index.loose_seed_members,
            lambda _seed, path, _cur: normalize_path_key(path) != current_key,
        )
        widened = bool(candidates)

    if not candidates:
        return None, False
    for seed, path in candidates:
        if seed > current_seed:
            return path, widened
    return candidates[0][1], widened


def _video_action_label(video_path: str, config: BridgeConfig) -> str:
    meta_path = metadata_path_for(video_path, config.provider_media_root, config.provider_metadata_root)
    if meta_path is None or not meta_path.is_file():
        return ""
    video = load_metadata(meta_path).get("video") or {}
    return str(video.get("action") or "").strip()


def _satellite_group_index(which: int, config: BridgeConfig, current: str) -> GroupIndex:
    """The cached grouping index over a satellite's sources, fresh for *current*."""
    sources = config.portrait_sources if which == 2 else config.landscape_sources
    return cached_group_index(
        sources,
        paths_supplier=lambda: collect_video_files(sources),
        media_root=config.provider_media_root,
        metadata_root=config.provider_metadata_root,
        must_contain=current,
    )


# Loop a satellite around one of the current clip's groups, instead of the
# whole playlist: its action group (the subject's other acts) or its seed
# family (the same act under other seeds).
_LOOP_COMMANDS: dict[str, tuple[int, str]] = {
    "portrait_action_loop": (2, "action"),
    "portrait_seed_loop": (2, "seed"),
    "landscape_action_loop": (3, "action"),
    "landscape_seed_loop": (3, "seed"),
}

_LOCK_ACTION_SIDES: dict[str, str] = {
    "portrait_lock_action": "portrait",
    "landscape_lock_action": "landscape",
}


def _dispatch_group_loop(
    which: int, axis: str, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Loop the satellite around the current clip's action group or seed family."""
    port = config.portrait_port if which == 2 else config.landscape_port
    source = _satellite_source(which)
    ops: list[WindowOp] = []
    current = target_path or get_current_file_path(port, config.vlc_password)
    if not current:
        return state, ops
    index = _satellite_group_index(which, config, current)
    gather = action_group_members if axis == "action" else seed_family_members
    members = [member for member in gather(index, current) if Path(member).exists()]
    if len(members) < 2:
        ops.append(WindowOp(op="notice", key=f"No other {axis}s", source=source))
        return state, ops
    # A loop is repeat-all over the group, so a repeat-one lock must go first.
    state = _cancel_lock(which, state, config)
    result = apply_satellite_loop(
        which=which,
        axis=axis,
        members=members,
        state_dir=config.state_dir,
        port=port,
        password=config.vlc_password,
    )
    logger.info(result.log_message)
    if result.applied:
        ensure_playback_state(port, config.vlc_password, should_play=True)
    ops.append(WindowOp(op="notice", key=result.log_message, source=source))
    return state, ops


def _dispatch_lock_action(
    scope: str, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Filter the satellite to the current clip's action — "portrait [act]",
    with the act read off the clip instead of spoken."""
    which = 2 if scope == "portrait" else 3
    port = config.portrait_port if which == 2 else config.landscape_port
    current = target_path or get_current_file_path(port, config.vlc_password)
    if not current:
        return state, []
    action = _video_action_label(current, config)
    if not action:
        return state, [WindowOp(op="notice", key="No action metadata", source=_satellite_source(which))]
    return _dispatch_set_filter(scope, action.lower(), state, config)


def _cycle_variant(
    which: int, kind: str, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Switch the satellite's current video to a sibling: another action of the
    same subject(s)+situation, or the same configuration under another seed.

    Unlike prev/next, cycling deliberately leaves an active lock alone: it
    means "show me this differently", not "move on" — the lock's repeat-one
    carries over to the sibling, which simply loops in its place.

    The siblings are those of *target_path* when a spoken command named a video
    the satellite has since advanced past: "show me this differently" is about
    the video the speaker saw, not its replacement.
    """
    port = config.portrait_port if which == 2 else config.landscape_port
    source = _satellite_source(which)
    ops: list[WindowOp] = []
    current = target_path or get_current_file_path(port, config.vlc_password)
    if not current:
        return state, ops
    index = _satellite_group_index(which, config, current)
    entries, _current_id = get_playlist_entries(port, config.vlc_password)
    widened = False
    if kind == "action":
        target = _next_action_sibling(index, current)
        missing_message = "No other actions"
    else:
        target, widened = _next_seed_sibling(index, current, entries)
        missing_message = "No other seeds"
    if target is None:
        ops.append(WindowOp(op="notice", key=missing_message, source=source))
        return state, ops
    if not _play_video(port, config.vlc_password, target, entries):
        logger.warning("cycle %s: could not switch to %s", kind, target)
        return state, ops
    ensure_playback_state(port, config.vlc_password, should_play=True)
    if kind == "action":
        # Numbered when the group holds several of the same act ("Alpha 2").
        action = action_label(index, target)
        if action:
            ops.append(WindowOp(op="notice", key=f"Action: {action}", source=source))
    elif widened:
        ops.append(WindowOp(op="notice", key="Similar clip", source=source))
    return state, ops


def command_side(command: str) -> int | None:
    """The player slot a command addresses: 1=primary, 2=portrait, 3=landscape —
    or None if it addresses no player.

    The primary (Nau) player only becomes active through its own next/prev
    navigation; it has no lock/weird/cycle, so nothing else selects it.
    """
    if command.startswith("portrait_"):
        return 2
    if command.startswith("landscape_"):
        return 3
    if command in ("primary_next", "primary_prev"):
        return 1
    return None


def dispatch_command(
    command: str,
    state: BridgeState,
    config: BridgeConfig,
    *,
    target_path: str = "",
) -> tuple[BridgeState, list[WindowOp]]:
    """Dispatch a dashboard/hotkey command, returning updated state and window operations.

    ``target_path`` names the video a spoken command was aimed at — the one on
    screen when the utterance began, which an auto-advancing satellite may have
    left behind by the time the phrase was recognized.  Every satellite action
    that is *about a particular video* honours it: lock, weird, cycle, the group
    loops and lock-action.  Navigation is relative rather than video-scoped, and
    the rest of the vocabulary names no video at all, so both ignore it.  Empty
    means "whatever is playing now", which is how every keyboard and dashboard
    command arrives.
    """
    ops: list[WindowOp] = []

    # Any explicit side command (voice or keyboard nav) becomes the active side,
    # so a later side-agnostic "active_*" command knows which player to drive.
    side = command_side(command)
    if side is not None:
        state = replace(state, active_side=side)

    cycle_target = _CYCLE_COMMANDS.get(command)
    if cycle_target is not None:
        which, kind = cycle_target
        return _cycle_variant(which, kind, state, config, target_path)

    loop_target = _LOOP_COMMANDS.get(command)
    if loop_target is not None:
        which, axis = loop_target
        return _dispatch_group_loop(which, axis, state, config, target_path)

    lock_action_scope = _LOCK_ACTION_SIDES.get(command)
    if lock_action_scope is not None:
        return _dispatch_lock_action(lock_action_scope, state, config, target_path)

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
        state, lock_ops = _toggle_lock(2, state, config, target_path)
        ops.extend(lock_ops)
        return state, ops

    if command == "portrait_trash":
        state = _discard(2, state, config, target_path)
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
        state, lock_ops = _toggle_lock(3, state, config, target_path)
        ops.extend(lock_ops)
        return state, ops

    if command == "landscape_trash":
        state = _discard(3, state, config, target_path)
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

    filter_target = decode_filter_command(command)
    if filter_target is not None:
        scope, query = filter_target
        return _dispatch_set_filter(scope, query, state, config)

    if command in ("genau_activate", "nau_activate", "hybrid_activate"):
        target = {"genau_activate": "genau", "nau_activate": "nau", "hybrid_activate": "hybrid"}[command]
        return _dispatch_mode_switch(target, state, config, ops)

    if command == "genau_toggle_auto":
        # Flip whether Genau may take over while OSR2 is in auto mode. The broker
        # reads this persisted flag each tick, so a plain file write is enough.
        _toggle_genau_enabled(genau_enabled_path(config.state_dir))
        return state, ops

    speed = _speed_engine_commands(command)
    if speed is not None:
        nau_cmd, genau_cmd, per_stretch = speed
        target = _speed_target(state, config, per_stretch=per_stretch)
        if target == "nau" and nau_cmd is not None:
            config.nau_cmd_file.write_text(nau_cmd, encoding="utf-8")
        elif target == "genau" and genau_cmd is not None:
            config.genau_cmd_file.write_text(genau_cmd, encoding="utf-8")
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
                ops.append(WindowOp(op="notice", key=msg, source=SOURCE_PRIMARY))
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
    """Visibility + z-order ops for the primary-slot windows on a mode switch.

    The two players (Nau and Genau) share one screen rect; exactly the mode's
    player(s) are shown and the inactive slot-mate hidden.  The new window is
    shown and activated BEFORE the old one hides so focus never falls through
    to another application.  Finally the pair is re-stacked for the new mode
    (``restack_primary``): Nau topmost, with Genau's HUD above it in hybrid.
    Nau and Genau overlap, so their z-order is explicit — unlike every other
    window, a plain topmost flag can't say "Genau above Nau, both on top."
    """
    restack = WindowOp(op="restack_primary")
    if primary_mode == "genau":
        return [
            WindowOp(op="show_role", key="genau"),
            WindowOp(op="activate_role", key="genau"),
            WindowOp(op="hide_role", key="nau"),
            restack,
        ]
    if primary_mode == "hybrid":
        return [
            WindowOp(op="show_role", key="nau"),
            WindowOp(op="show_role", key="genau"),
            WindowOp(op="activate_role", key="genau"),
            restack,
        ]
    return [
        WindowOp(op="show_role", key="nau"),
        WindowOp(op="activate_role", key="nau"),
        WindowOp(op="hide_role", key="genau"),
        restack,
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
        portrait_filter=state.portrait_filter,
        landscape_filter=state.landscape_filter,
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
        portrait_filter=state.portrait_filter,
        landscape_filter=state.landscape_filter,
        provider_media_root=config.provider_media_root,
        provider_metadata_root=config.provider_metadata_root,
    )
    if result.log_message:
        logger.info(result.log_message)
    return replace(
        state,
        recency_order=result.next_recency_order,
        locked2=result.next_locked2,
        locked3=result.next_locked3,
    ), []


def _dispatch_set_filter(
    scope: str, query: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Apply a metadata filter to one or both satellites and rebuild them.

    ``scope`` is both/portrait/landscape; ``query`` is the substring to match
    ("" clears).  Each targeted satellite records its own filter in the state so
    later F-mode / premiere rebuilds keep it, then reloads under the current
    ordering.
    """
    targets = {"both": (2, 3), "portrait": (2,), "landscape": (3,)}[scope]
    ops: list[WindowOp] = []
    for which in targets:
        sources = config.portrait_sources if which == 2 else config.landscape_sources
        port = config.portrait_port if which == 2 else config.landscape_port
        result = apply_satellite_filter(
            which=which,
            query=query,
            f_mode_enabled=state.f_mode_enabled,
            recent=state.recency_order,
            sources=sources,
            favs_file=config.favs_file,
            state_dir=config.state_dir,
            port=port,
            password=config.vlc_password,
            provider_media_root=config.provider_media_root,
            provider_metadata_root=config.provider_metadata_root,
        )
        # Only remember a filter that actually selected videos: a zero-match
        # filter left the current playlist alone, so recording it would let the
        # next F-mode/premiere rebuild blank the VLC.
        if result.applied:
            if which == 2:
                state = replace(state, portrait_filter=query)
            else:
                state = replace(state, landscape_filter=query)
        logger.info(result.log_message)
        ops.append(WindowOp(op="notice", key=result.log_message, source=_satellite_source(which)))
    return state, ops


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
