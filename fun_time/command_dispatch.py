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

from .audio_volume import MAX_VOLUME, MIN_VOLUME, VOLUME_STEP, write_volume
from .config import ProviderRegenConfig
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
    widened_seed_members,
)
from .dashboard_runtime import genau_enabled_path, read_genau_enabled, read_nau_status
from .lock import build_lock_plan
from .lock_hud import cell_path, hud_map_cells, locate_cell, navigate_cell
from .modes import collect_video_files, write_playlist_file
from .random_favs_browser import FavEntry, target_for_fav
from .rfb_tab_page import tabs_dir, write_lock_tab_page
from .mode_plan import genau_active, nau_displays
from .filter_vocab import decode_filter_command
from .runtime_flow import (
    SatelliteFilterFlowResult,
    apply_enter_omnipause,
    apply_leave_omnipause,
    apply_mode_switch,
    apply_satellite_filter,
    apply_toggle_fmode,
    build_omnipause_toggle,
    satellite_browse_paths,
)
from .satellite_control import read_satellite_status, write_satellite_command
from .watch_stats import record_watch_event, watch_stats_path
from .event_log import (
    NOTICE,
    SOURCE_LANDSCAPE,
    SOURCE_PORTRAIT,
    SOURCE_PRIMARY,
    SOURCE_SYSTEM,
)

logger = logging.getLogger(__name__)

# A notice that reports a command had no effect ("No other seeds") is logged at
# ERROR so the log panel and the on-player flash render it red, not green — the
# user asked to tell a command that did something from one that hit a dead end at
# a glance.
FAILED_NOTICE_LEVEL = logging.ERROR


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
    # Which browse order each satellite is in: newest-first ("Latest") when set,
    # else shuffled.  Per side, since Latest and Shuffle name a side, and read by
    # every later rebuild (a filter, F-mode) so the side reloads the same way.
    portrait_latest: bool = False
    landscape_latest: bool = False
    # The player most recently navigated (1=primary/Nau, 2=portrait,
    # 3=landscape). Any portrait_/landscape_ command, or a primary next/prev,
    # updates it; the side-agnostic "active_*" commands resolve against it —
    # nav (next/prev) reaches all three, the satellite-only actions only 2/3.
    active_side: int = 2
    # Per-satellite metadata filter queries ("" = no filter). Persisted in the shared
    # state file so they survive the dispatch loop's per-tick state resync and
    # are honoured by later F-mode / reorder rebuilds.
    portrait_filter: str = ""
    landscape_filter: str = ""
    # Which group loop each satellite is running: "" none, "action" (looping the
    # action column) or "seed" (looping the seed row).  A loop is repeat-all over
    # a sub-playlist; the satellite's own auto-advance keeps it alive, but any dispatch
    # command that rebuilds or re-navigates the side drops it.  Persisted so the
    # HUD can freeze its map on the looped group and keep the loop button lit
    # while the clip auto-advances.
    portrait_loop: str = ""
    landscape_loop: str = ""
    # The clip each satellite's HUD map hangs on — the head of the queue a loop
    # wrote.  The map is ordered from it, so the group is drawn in the order the
    # player actually plays it: the clip you pressed loop on in the corner, the rest
    # walking away from it.  It outlives the loop: switching a loop off leaves the
    # map hanging here, so only the loop's own chrome goes, and the map re-homes on
    # its own once the browse moves on past the group.
    portrait_map_anchor: str = ""
    landscape_map_anchor: str = ""
    # The clip each satellite's seed row has been widened around ("more seeds").
    # While it equals the clip on screen the HUD shows the near-matches ranked in
    # alongside the family; navigating to another clip leaves it behind, so the
    # widen auto-resets without any explicit clear.
    portrait_widen_clip: str = ""
    landscape_widen_clip: str = ""
    # The clip each satellite's HUD map is frozen on for keyboard navigation ("" =
    # not navigating).  The arrow / WASD keys move a selection across that frozen
    # map, switching the satellite to each cell's clip; the anchor holds until Enter
    # locks the selection, another command re-homes the side, or the satellite
    # drifts off the map.  Persisted so the HUD freezes its map to match.
    portrait_nav_anchor: str = ""
    landscape_nav_anchor: str = ""
    # The primary display's sound level, 0-100, and whether it is silenced.  A
    # mute leaves the level alone so a second "mute" restores what was set.
    volume: int = MAX_VOLUME
    muted: bool = False


@dataclass
class BridgeConfig:
    # Each satellite (2=portrait, 3=landscape) is a native mpv-backed player
    # driven through a file quartet — a command file it drains verbs from, a
    # paused flag it obeys, a status file it publishes, and the playlist file it
    # plays.  See :mod:`fun_time.satellite_control`.
    portrait_cmd_file: Path
    portrait_paused_file: Path
    portrait_status_file: Path
    portrait_playlist_file: Path
    landscape_cmd_file: Path
    landscape_paused_file: Path
    landscape_status_file: Path
    landscape_playlist_file: Path
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
    audio_volume_file: Path
    nau_cmd_file: Path
    nau_paused_file: Path
    nau_status_file: Path
    dashboard_state_file: Path
    # Where Nau publishes its one-shot notices (a clip jump with nowhere to go).
    nau_notice_file: Path | None = None
    broker_cmd_file: Path | None = None
    broker_heartbeat_file: Path | None = None
    broker_tray_launcher: Path | None = None
    provider_media_root: Path | None = None
    provider_metadata_root: Path | None = None
    provider_generate_video_url: str = "https://example.com/video"
    provider_generate_image_url: str = "https://example.com/create"

    def satellite_cmd_file(self, which: int) -> Path:
        return self.portrait_cmd_file if which == 2 else self.landscape_cmd_file

    def satellite_status_file(self, which: int) -> Path:
        return self.portrait_status_file if which == 2 else self.landscape_status_file

    def satellite_playlist_file(self, which: int) -> Path:
        return self.portrait_playlist_file if which == 2 else self.landscape_playlist_file

    @property
    def provider_regen(self) -> ProviderRegenConfig:
        """The four Provider settings, in the shape the regenerate code expects."""
        return ProviderRegenConfig(
            generate_video_url=self.provider_generate_video_url,
            generate_image_url=self.provider_generate_image_url,
            media_root=self.provider_media_root,
            metadata_root=self.provider_metadata_root,
        )


@dataclass(frozen=True)
class WindowOp:
    op: str
    # The op's one payload: a role name, an RFB URL, or a notice's message.
    key: str = ""
    # Which window a ``notice`` op is about, so the log panel can filter it.
    source: str = SOURCE_SYSTEM
    # The log level a ``notice`` op is logged at — NOTICE (green) for a normal
    # confirmation, ERROR (red) for a command that hit a dead end.
    level: int = NOTICE


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
    "nau_length_mixed": "SET_LENGTH_MODE mixed",
    "nau_compilation": "PLAY_COMPILATION",
    "nau_full_vid": "PLAY_FULL_VID",
    "nau_money_shot": "PLAY_MONEY_SHOT",
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


def _satellite_current(config: BridgeConfig, which: int) -> str:
    """The video a satellite is showing now, read from its published status file."""
    return read_satellite_status(config.satellite_status_file(which)).video


def _send_satellite(config: BridgeConfig, which: int, verb: str) -> None:
    """Queue one verb on a satellite's command file for the player to drain."""
    write_satellite_command(config.satellite_cmd_file(which), verb)


def _play_video(config: BridgeConfig, which: int, path: str) -> None:
    """Make *path* the satellite's current clip.

    ``PLAY_FILE`` is the native player's jump-or-splice: it jumps to the clip if
    it is already queued, else splices it in after the current clip and plays it.
    """
    _send_satellite(config, which, f"PLAY_FILE {path}")


def _cancel_lock(which: int, state: BridgeState, config: BridgeConfig) -> BridgeState:
    """Release a repeat-one lock so the side auto-advances again.

    A locked satellite is holding one clip (``LOCK`` → mpv ``loop_file``); the
    ``UNLOCK`` verb restores end-of-file playlist advance.  A no-op when the side
    was not locked.
    """
    locked = state.locked2 if which == 2 else state.locked3
    if locked:
        _send_satellite(config, which, "UNLOCK")
    return replace(state, locked2=False) if which == 2 else replace(state, locked3=False)


def _toggle_lock(
    which: int, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    locked = state.locked2 if which == 2 else state.locked3
    current_path = _satellite_current(config, which)
    # "Lock" names the video the speaker had in front of them.  If the satellite
    # auto-advanced while the phrase was being recognized, bring that video back
    # and lock it — the whole point of the lock is to keep watching *it*.  An
    # unlock needs no such rescue: a locked satellite repeats one video and
    # cannot have advanced.
    if not locked and target_path and not _same_video(target_path, current_path):
        _play_video(config, which, target_path)
        logger.info("Lock back-dated to %s (player %d had advanced)", target_path, which)
        current_path = target_path
    plan = build_lock_plan("toggle-lock", which=which, locked=locked, current_path=current_path)
    _send_satellite(config, which, "LOCK" if plan.next_locked else "UNLOCK")
    if plan.ensure_in_favs and current_path:
        ensure_in_favs(config.favs_file, current_path)
        # Locking is the strongest positive watch signal ("breeding" weight).
        record_watch_event(watch_stats_path(config.state_dir), current_path, "lock")
    if plan.advance_playlist:
        # Unlocking moves on from the clip you were dwelling on, rather than
        # replaying it once more before the auto-advance.
        _send_satellite(config, which, "NEXT")
    if plan.log_message:
        logger.info(plan.log_message)
    lock_ops: list[WindowOp] = []
    if plan.open_rfb_tab and current_path:
        # Resolved exactly like an RFB startup tab, and deferred behind the same
        # Ctrl+R landing page, so a lock never drops a heavy generate page on you.
        target = target_for_fav(
            FavEntry(local_path=current_path, web_url=make_web_url_from_path(current_path)),
            config.provider_regen,
        )
        if target.url:
            uri = write_lock_tab_page(tabs_dir(config.state_dir), target)
            lock_ops.append(WindowOp(op="open_rfb_tab", key=uri))
    # A lock is repeat-one on a single clip — incompatible with a group loop's
    # repeat-all — so toggling the lock ends any loop (and widened row) the side
    # was running.
    next_state = replace(state, locked2=plan.next_locked) if which == 2 else replace(state, locked3=plan.next_locked)
    return _clear_side_grouping(next_state, which), lock_ops


def _discard(
    which: int, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> BridgeState:
    locked = state.locked2 if which == 2 else state.locked3
    current_path = _satellite_current(config, which)
    # "Weird" condemns the video the speaker saw.  When the satellite advanced
    # while the phrase was being recognized, jump back to the condemned clip
    # before trashing it, so the wrong (innocent) clip is never the one dropped.
    condemned = target_path or current_path
    already_moved_on = bool(target_path) and not _same_video(target_path, current_path)
    plan = build_lock_plan("discard", which=which, locked=locked, current_path=condemned)
    if locked:
        # A locked satellite is repeat-one; drop the lock so TRASH advances into
        # the playlist instead of looping the clip that replaced the discarded one.
        _send_satellite(config, which, "UNLOCK")
    if plan.remove_from_favs and condemned:
        remove_from_favs(config.favs_file, condemned)
    if plan.advance_playlist:
        if already_moved_on:
            _play_video(config, which, condemned)
        # TRASH drops the current clip from the playlist and plays the next.
        _send_satellite(config, which, "TRASH")
    if plan.move_to_weird and condemned:
        move_to_weird(config.weird_dir, Path(condemned))
    if plan.log_message:
        logger.info(plan.log_message)
    return replace(state, locked2=False) if which == 2 else replace(state, locked3=False)


# display slot (2=portrait, 3=landscape) and variation axis per cycle command.
_CYCLE_COMMANDS = {
    "portrait_cycle_action": (2, "action"),
    "portrait_cycle_seed": (2, "seed"),
    "landscape_cycle_action": (3, "action"),
    "landscape_cycle_seed": (3, "seed"),
}

# "more seeds" is cycle-seed with the net widened to same-scene near-matches —
# the manual trigger for what cycle-seed used to do automatically.
_MORE_SEEDS_SIDES = {"portrait_more_seeds": 2, "landscape_more_seeds": 3}


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


def _next_seed_sibling(index: GroupIndex, current: str) -> str | None:
    """The next exact seed sibling of *current* — a different seed of the
    identical config — toured in seed order (the first seed above the current
    one, wrapping to the lowest).  None when there is no sister; the HUD shows
    exactly which sisters exist, and widening the net is a separate, HUD-only
    action ("more seeds"), not a cycle.
    """
    current_key = normalize_path_key(current)
    current_action = index.action_by_path.get(current_key, "")
    entry = index.seed_key_by_path.get(current_key)
    if entry is None:
        return None
    family, current_seed = entry
    found: list[tuple[str, str]] = []
    for path in (m for m in index.seed_members.get(family, []) if Path(m).exists()):
        key = normalize_path_key(path)
        candidate = index.seed_key_by_path.get(key)
        # Same action only. An image-to-video seed family is keyed on the source
        # image alone, so it spans actions; but the seed axis is "the same act,
        # another subject", so a sister seed doing a different act belongs on the
        # action axis, not here. This keeps the walk in step with what
        # seed_family_members draws in the HUD.
        if (
            candidate
            and candidate[0] == family
            and index.action_by_path.get(key, "") == current_action
            and candidate[1] != current_seed
        ):
            found.append((candidate[1], path))
    found.sort()
    if not found:
        return None
    for seed, path in found:
        if seed > current_seed:
            return path
    return found[0][1]


def _video_action_label(video_path: str, config: BridgeConfig) -> str:
    meta_path = metadata_path_for(video_path, config.provider_metadata_root)
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

# "reset" clears a satellite's filter and reshuffles it back to the default
# browse; "both reset" expands to these two before dispatch.
_RESET_SIDES: dict[str, str] = {
    "portrait_reset": "portrait",
    "landscape_reset": "landscape",
}

# "no loop" ends a group loop but, unlike reset, keeps the satellite's filter.
_NO_LOOP_SIDES: dict[str, str] = {
    "portrait_no_loop": "portrait",
    "landscape_no_loop": "landscape",
}

# The two browse orderings, per side: Latest reloads newest-first, Shuffle
# reshuffles.  "both …" reaches each of these in turn (the dispatch loop expands
# it), which is what the P key sends.
_REORDER_COMMANDS: dict[str, tuple[int, bool]] = {
    "portrait_latest": (2, True),
    "landscape_latest": (3, True),
    "portrait_shuffle": (2, False),
    "landscape_shuffle": (3, False),
}


def _set_side_latest(state: BridgeState, which: int, recent: bool) -> BridgeState:
    """Record which order *which* satellite's browse is now in."""
    if which == 2:
        return replace(state, portrait_latest=recent)
    return replace(state, landscape_latest=recent)


def _set_side_loop(state: BridgeState, which: int, axis: str, anchor: str) -> BridgeState:
    """Record that *which* satellite is running *axis*'s group loop, started on
    *anchor* — the clip that heads the queue the loop just wrote."""
    if which == 2:
        return replace(state, portrait_loop=axis, portrait_map_anchor=anchor)
    return replace(state, landscape_loop=axis, landscape_map_anchor=anchor)


def _clear_side_grouping(state: BridgeState, which: int) -> BridgeState:
    """Forget any group loop AND any widened seed row on *which* satellite — its
    playlist was rebuilt or re-navigated, which drops both.  A no-op when neither
    was set.  The widen only ever means something in the context of the clip/loop
    it was taken around, so a rebuild that drops the loop drops the widen with it."""
    if which == 2:
        return replace(state, portrait_loop="", portrait_map_anchor="", portrait_widen_clip="")
    return replace(state, landscape_loop="", landscape_map_anchor="", landscape_widen_clip="")


def _set_side_widen(state: BridgeState, which: int, clip: str) -> BridgeState:
    """Record that *which* satellite's seed row is widened around *clip* ("" clears)."""
    if which == 2:
        return replace(state, portrait_widen_clip=clip)
    return replace(state, landscape_widen_clip=clip)


def _dispatch_more_seeds(
    which: int, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Widen the seed row the HUD draws around the current clip — "more seeds".

    This does NOT change what is playing; it records that this clip's net is
    widened, and the HUD redraws its seed row with the near-matches ranked in.  It
    then loops that pool — the point of a wider row is to cycle it — which also
    re-shapes a seed loop that was already running onto exactly what the HUD now
    shows.  Only a library holding nothing but this clip can fail to widen, so the
    dead end is a real "there is no other video", not "nothing matched"."""
    source = _satellite_source(which)
    current = target_path or _satellite_current(config, which)
    if not current:
        return state, []
    index = _satellite_group_index(which, config, current)
    current_key = normalize_path_key(current)
    exact = {normalize_path_key(m) for m in seed_family_members(index, current)} - {current_key}
    wide = {normalize_path_key(m) for m in widened_seed_members(index, current)} - {current_key}
    if wide <= exact:
        return state, [WindowOp(op="notice", key="Widening net failed", source=source, level=FAILED_NOTICE_LEVEL)]
    state = _set_side_widen(state, which, current)
    # Loop the pool that was just widened: the widen anchor now matches the clip on
    # screen, so the loop gathers the wider row the HUD draws.  This starts a loop
    # where none was running and re-shapes one that was.  Its notices are dropped —
    # "More seeds" is the one thing that happened, from the user's side.
    state, _loop_ops = _dispatch_group_loop(which, "seed", state, config, target_path=current)
    return state, [WindowOp(op="notice", key="More seeds", source=source)]


def _dispatch_group_loop(
    which: int, axis: str, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Loop the satellite around the current clip's action group or seed family."""
    source = _satellite_source(which)
    ops: list[WindowOp] = []
    current = target_path or _satellite_current(config, which)
    if not current:
        return state, ops
    index = _satellite_group_index(which, config, current)
    # Loop what the HUD is showing: if the seed row has been widened around this
    # very clip ("more seeds"), loop that wider pool, not just the exact family.
    widen_clip = state.portrait_widen_clip if which == 2 else state.landscape_widen_clip
    widened = axis == "seed" and normalize_path_key(widen_clip) == normalize_path_key(current)
    gather = widened_seed_members if widened else (
        action_group_members if axis == "action" else seed_family_members
    )
    members = [member for member in gather(index, current) if Path(member).exists()]
    if len(members) < 2:
        # Only this clip is in the group, so "looping" it is a single-video lock:
        # LOCK this one.  Never a dead end — the loop buttons are still valid with
        # one video, they just mean "lock" then.  A lock is not a loop, so any
        # prior loop (and widened row) is dropped.
        _send_satellite(config, which, "LOCK")
        state = replace(state, locked2=True) if which == 2 else replace(state, locked3=True)
        state = _clear_side_grouping(state, which)
        return state, [WindowOp(op="notice", key="Locked", source=source)]
    # A loop is repeat-all over the group, so a repeat-one lock must go first.
    state = _cancel_lock(which, state, config)
    # Write the group as the side's playlist with the current clip first, then
    # RELOAD_PLAYLIST: the native player keeps the current clip playing when it
    # survives the reload, so the clip on screen is never restarted and only what
    # comes up next becomes the group, which then cycles by auto-advance.
    members = [current] + [m for m in members if normalize_path_key(m) != normalize_path_key(current)]
    write_playlist_file(config.satellite_playlist_file(which), members)
    _send_satellite(config, which, "RELOAD_PLAYLIST")
    label = "portrait" if which == 2 else "landscape"
    message = f"Loop {label}: {len(members)} {axis}s"
    logger.info(message)
    state = _set_side_loop(state, which, axis, current)
    # Anchor the widen on the loop iff it is the loose family being looped, so the
    # HUD reads a running seed loop as widened exactly when it truly is — and a
    # plain exact-family loop drops any stale anchor.
    state = _set_side_widen(state, which, current if widened else "")
    ops.append(WindowOp(op="notice", key=message, source=source))
    return state, ops


def _dispatch_lock_action(
    scope: str, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Filter the satellite to the current clip's action — "portrait [act]",
    with the act read off the clip instead of spoken."""
    which = 2 if scope == "portrait" else 3
    current = target_path or _satellite_current(config, which)
    if not current:
        return state, []
    action = _video_action_label(current, config)
    if not action:
        return state, [WindowOp(op="notice", key="No action metadata", source=_satellite_source(which), level=FAILED_NOTICE_LEVEL)]
    return _dispatch_set_filter(scope, action.lower(), state, config)


def _dispatch_play_video(
    which: int, path: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Switch a satellite straight to *path* — the command a HUD thumbnail click
    sends.  Plays it from the playlist if it is already there, else splices it in
    after the current clip, exactly as cycling to a sibling does."""
    if not path:
        return state, []
    _play_video(config, which, path)
    return state, [WindowOp(op="notice", key="Switched", source=_satellite_source(which))]


def _dispatch_lock_video(
    which: int, path: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Double-click a HUD thumbnail: switch to *path* and lock it (repeat-one).

    When already locked the satellite is repeat-one, so playing the picked clip
    just moves the lock onto it; when unlocked, toggling the lock with the target
    both switches to it and locks it (the same back-dating a spoken "lock" uses).
    """
    locked = state.locked2 if which == 2 else state.locked3
    if locked:
        return _dispatch_play_video(which, path, state, config)
    return _toggle_lock(which, state, config, target_path=path)


# Keyboard navigation of the HUD map.  "<side>_nav_<dir>" moves a selection
# around the frozen map (right/left walk the seed row, down/up the action
# column) and switches the satellite to the picked cell; "<side>_nav_lock"
# (Enter) locks the selection and re-homes the map on it.
_NAV_DIRECTIONS = ("left", "right", "up", "down")
_NAV_LOCK_SIDES = {"portrait_nav_lock": 2, "landscape_nav_lock": 3}


def _parse_nav(command: str) -> tuple[int, str] | None:
    """``(slot, direction)`` for a ``<side>_nav_<dir>`` command, else None."""
    for prefix, which in (("portrait_nav_", 2), ("landscape_nav_", 3)):
        if command.startswith(prefix):
            direction = command[len(prefix):]
            if direction in _NAV_DIRECTIONS:
                return which, direction
    return None


def _is_hud_nav_command(command: str) -> bool:
    """Whether *command* drives HUD keyboard navigation — a direction step or the
    Enter lock.  Those manage the side's nav anchor themselves, so the generic
    "any other side command re-homes the map" rule leaves them alone."""
    return _parse_nav(command) is not None or command in _NAV_LOCK_SIDES


def _nav_anchor(state: BridgeState, which: int) -> str:
    return state.portrait_nav_anchor if which == 2 else state.landscape_nav_anchor


def _set_nav_anchor(state: BridgeState, which: int, anchor: str) -> BridgeState:
    if which == 2:
        return replace(state, portrait_nav_anchor=anchor)
    return replace(state, landscape_nav_anchor=anchor)


def _clear_nav_anchor(state: BridgeState, which: int) -> BridgeState:
    return _set_nav_anchor(state, which, "")


def _navigate_hud(
    which: int, direction: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Move the HUD map's keyboard selection one step and switch the satellite to
    the picked clip, keeping the map frozen on the clip navigation began from.

    The selection is wherever the satellite is now playing on the frozen map; the
    step lands on a neighbouring cell, whose clip becomes the new current video.
    A satellite that auto-advanced off the frozen map re-anchors on whatever is
    now playing.  Each axis wraps, so running off its end comes round to the
    anchor; only a step with genuinely nowhere to go — off the axis the selection
    is on, or along an axis holding just the anchor — is a dead end, reported red
    like the other no-effect notices.
    """
    source = _satellite_source(which)
    current = _satellite_current(config, which)
    if not current:
        return state, []
    index = _satellite_group_index(which, config, current)
    anchor = _nav_anchor(state, which)
    if anchor:
        seeds, actions = hud_map_cells(index, anchor)
        if locate_cell(current, anchor, seeds, actions) is None:
            anchor = ""  # drifted off the frozen map — start over from the live clip
    if not anchor:
        anchor = current
    seeds, actions = hud_map_cells(index, anchor)
    cell = locate_cell(current, anchor, seeds, actions) or ("corner", 0)
    target_cell = navigate_cell(cell, direction, seed_count=len(seeds), action_count=len(actions))
    target = cell_path(target_cell, anchor, seeds, actions)
    state = _set_nav_anchor(state, which, anchor)
    if target_cell == cell or not target or _same_video(target, current):
        return state, [WindowOp(op="notice", key="No clip that way", source=source, level=FAILED_NOTICE_LEVEL)]
    return _dispatch_play_video(which, target, state, config)


def _dispatch_nav_lock(
    which: int, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Enter: lock the selected clip and re-home the map on it (like a
    double-click).  The selection is whatever the satellite is now playing, so
    this locks the current clip and drops the frozen nav anchor, letting the HUD
    re-home its map on the freshly locked clip."""
    state = _clear_nav_anchor(state, which)
    current = _satellite_current(config, which)
    if not current:
        return state, []
    return _dispatch_lock_video(which, current, state, config)


def _cycle_variant(
    which: int, kind: str, state: BridgeState, config: BridgeConfig,
    target_path: str = "",
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
    source = _satellite_source(which)
    ops: list[WindowOp] = []
    current = target_path or _satellite_current(config, which)
    if not current:
        return state, ops
    index = _satellite_group_index(which, config, current)
    if kind == "action":
        target = _next_action_sibling(index, current)
        missing_message = "No other actions"
    else:
        target = _next_seed_sibling(index, current)
        missing_message = "No other seeds"
    if target is None:
        ops.append(WindowOp(op="notice", key=missing_message, source=source, level=FAILED_NOTICE_LEVEL))
        return state, ops
    _play_video(config, which, target)
    if kind == "action":
        # Numbered when the group holds several of the same act ("Alpha 2").
        action = action_label(index, target)
        if action:
            ops.append(WindowOp(op="notice", key=f"Action: {action}", source=source))
    else:
        ops.append(WindowOp(op="notice", key="Next seed", source=source))
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
        # Every side command except a navigation step ends keyboard navigation on
        # that side, so its map re-homes on the live clip; nav commands manage
        # their own anchor.
        if not _is_hud_nav_command(command):
            state = _clear_nav_anchor(state, side)

    # Keyboard navigation of the HUD map: "<side>_nav_<dir>" moves the selection
    # and switches the satellite; "<side>_nav_lock" (Enter) locks the selection.
    nav = _parse_nav(command)
    if nav is not None:
        return _navigate_hud(*nav, state, config)
    nav_lock_side = _NAV_LOCK_SIDES.get(command)
    if nav_lock_side is not None:
        return _dispatch_nav_lock(nav_lock_side, state, config)

    # A HUD thumbnail click sends "<side>_play_video|<path>": switch straight to
    # that clip. The path is carried after the "|" ("|" is illegal in a Windows
    # path, so it is an unambiguous delimiter).
    if "_play_video|" in command:
        head, _, path = command.partition("|")
        return _dispatch_play_video(2 if head.startswith("portrait_") else 3, path, state, config)

    # Double-click of a HUD thumbnail: "<side>_lock_video|<path>" — switch and lock.
    if "_lock_video|" in command:
        head, _, path = command.partition("|")
        return _dispatch_lock_video(2 if head.startswith("portrait_") else 3, path, state, config)

    cycle_target = _CYCLE_COMMANDS.get(command)
    if cycle_target is not None:
        which, kind = cycle_target
        return _cycle_variant(which, kind, state, config, target_path)

    more_seeds_side = _MORE_SEEDS_SIDES.get(command)
    if more_seeds_side is not None:
        return _dispatch_more_seeds(more_seeds_side, state, config, target_path)

    loop_target = _LOOP_COMMANDS.get(command)
    if loop_target is not None:
        which, axis = loop_target
        return _dispatch_group_loop(which, axis, state, config, target_path)

    no_loop_scope = _NO_LOOP_SIDES.get(command)
    if no_loop_scope is not None:
        return _dispatch_no_loop(no_loop_scope, state, config)

    lock_action_scope = _LOCK_ACTION_SIDES.get(command)
    if lock_action_scope is not None:
        return _dispatch_lock_action(lock_action_scope, state, config, target_path)

    if command == "portrait_prev":
        state = _cancel_lock(2, state, config)
        _send_satellite(config, 2, "PREV")
        return state, ops

    if command == "portrait_next":
        state = _cancel_lock(2, state, config)
        _send_satellite(config, 2, "NEXT")
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
        _send_satellite(config, 3, "PREV")
        return state, ops

    if command == "landscape_next":
        state = _cancel_lock(3, state, config)
        _send_satellite(config, 3, "NEXT")
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

    if command in _MUTE_COMMANDS:
        return _dispatch_audio(replace(state, muted=_MUTE_COMMANDS[command]), config)

    step = _VOLUME_STEPS.get(command)
    if step is not None:
        return _dispatch_audio(_step_volume(state, step), config)

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

    reorder = _REORDER_COMMANDS.get(command)
    if reorder is not None:
        which, recent = reorder
        return _dispatch_reorder(which, recent, state, config)

    reset_scope = _RESET_SIDES.get(command)
    if reset_scope is not None:
        return _dispatch_reset(reset_scope, state, config)

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


_VOLUME_STEPS = {"audio_volume_down": -VOLUME_STEP, "audio_volume_up": VOLUME_STEP}

# Mute and unmute each assert a state rather than toggling one, so a phrase
# misheard twice cannot leave the sound the opposite of what was asked for.
_MUTE_COMMANDS = {"audio_mute": True, "audio_unmute": False}


def _step_volume(state: BridgeState, step: int) -> BridgeState:
    """Move the sound level by *step*, staying within the silent/full bounds.

    Asking for a loudness lifts a mute, as reaching for the volume does in the
    Windows mixer: a "louder" that left the room silent would read as the
    command having been missed.
    """
    volume = max(MIN_VOLUME, min(MAX_VOLUME, state.volume + step))
    return replace(state, volume=volume, muted=False)


def _dispatch_audio(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Publish *state*'s sound level to both of the primary display's audio sinks.

    Nau's mpv carries the video's sound; the Genau audio companion carries the
    clip music.  Which one is audible depends on the mode, so both are told the
    same level every time and the bridge alone holds the authoritative value.
    A mute is published as a level of zero rather than as a flag of its own —
    the sinks stay dumb, and the level the speaker chose survives underneath.
    """
    level = MIN_VOLUME if state.muted else state.volume
    config.nau_cmd_file.write_text(f"SET_VOLUME {level}", encoding="utf-8")
    write_volume(config.audio_volume_file, level)
    message = "Muted" if state.muted else f"Volume {state.volume}%"
    return state, [WindowOp(op="notice", key=message, source=SOURCE_PRIMARY)]


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
            portrait_paused_file=config.portrait_paused_file,
            landscape_paused_file=config.landscape_paused_file,
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
            portrait_paused_file=config.portrait_paused_file,
            landscape_paused_file=config.landscape_paused_file,
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
        portrait_paused_file=config.portrait_paused_file,
        landscape_paused_file=config.landscape_paused_file,
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
        portrait_paused_file=config.portrait_paused_file,
        landscape_paused_file=config.landscape_paused_file,
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
        portrait_recent=state.portrait_latest,
        landscape_recent=state.landscape_latest,
        primary_sources=config.primary_sources,
        portrait_sources=config.portrait_sources,
        landscape_sources=config.landscape_sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        portrait_cmd_file=config.portrait_cmd_file,
        landscape_cmd_file=config.landscape_cmd_file,
        nau_cmd_file=config.nau_cmd_file,
        provider_media_root=config.provider_media_root,
        provider_metadata_root=config.provider_metadata_root,
        portrait_filter=state.portrait_filter,
        landscape_filter=state.landscape_filter,
    )
    if result.log_message:
        logger.info(result.log_message)
    # F-mode rebuilds both satellites' playlists, dropping any group loops and the
    # widened seed rows that rode on them.
    return replace(
        state,
        f_mode_enabled=result.next_f_mode_enabled,
        locked2=result.next_locked2,
        locked3=result.next_locked3,
        portrait_loop="",
        landscape_loop="",
        portrait_widen_clip="",
        landscape_widen_clip="",
    ), []


def _dispatch_reorder(
    which: int, recent: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Reload one satellite in a fresh order — Latest (newest-first) or Shuffle.

    Either rescans that side's sources, so clips that have arrived since are picked
    up, and keeps its filter.  The order is remembered per side — the two satellites
    can be in different ones — so a later filter or F-mode rebuild reloads it the
    same way.  The rebuild replaces the queue, which drops the side's lock and any
    group loop (with the widened row that rode on it).
    """
    state = _set_side_latest(state, which, recent)
    result = _rebuild_side(which, _side_filter(state, which), state, config)
    state = replace(state, locked2=False) if which == 2 else replace(state, locked3=False)
    state = _clear_side_grouping(state, which)
    label = "portrait" if which == 2 else "landscape"
    message = f"{'Latest' if recent else 'Shuffle'}: {label} {'newest-first' if recent else 'reshuffled'}"
    logger.info("%s (%d clips)", message, result.count)
    return state, [WindowOp(op="notice", key=message, source=_satellite_source(which))]


def _dispatch_reset(
    scope: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Return a satellite (or both) to the default browse: no filter, no Latest
    order, no loop — reshuffled, one clip per subject.  Clearing the filter rebuilds
    the full playlist, which also drops any group loop."""
    for which in _FILTER_TARGETS[scope]:
        state = _set_side_latest(state, which, False)
    return _dispatch_set_filter(scope, "", state, config)


def _browse_behind(browse: list[str], current: str) -> list[str]:
    """*browse*, guaranteed to still hold *current* — the clip on screen.

    The player keeps its clip across a playlist reload only while the new list
    still holds it, and a loop member usually is not in the browse: the browse
    picks one clip per group and the loop was cycling that group's others.  So the
    clip on screen heads the restored list — it plays to its own end and the browse
    is simply what comes up next.  A browse that already holds it keeps its own
    order, which the reload resumes from wherever the clip sits.
    """
    if not current or any(_same_video(path, current) for path in browse):
        return browse
    return [current, *browse]


def _dispatch_no_loop(
    scope: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """End a group loop, returning the queue to the browse — but keep the filter.

    A loop shrank the queue to the group; ending it reshapes the queue back to
    the satellite's default browse *in place*, so the clip on screen keeps
    playing to its end and only what comes up next returns to browsing.  The
    satellite's own filter is kept (reset, by contrast, also clears it), so the
    restored browse still honours it.
    """
    which = 2 if scope == "portrait" else 3
    current = _satellite_current(config, which)
    browse = satellite_browse_paths(
        which=which,
        query=_side_filter(state, which),
        f_mode_enabled=state.f_mode_enabled,
        recent=state.portrait_latest if which == 2 else state.landscape_latest,
        sources=config.portrait_sources if which == 2 else config.landscape_sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        provider_metadata_root=config.provider_metadata_root,
    )
    # A non-empty filter that now matches nothing would blank the queue, so the
    # browse is only reshaped when it actually has clips; otherwise the loop's
    # queue keeps playing and just the flag clears.
    if browse:
        write_playlist_file(config.satellite_playlist_file(which), _browse_behind(browse, current))
        _send_satellite(config, which, "RELOAD_PLAYLIST")
    # Only the loop itself goes.  The map anchor and any widened row stay, so the HUD
    # keeps hanging exactly where it was and switching a loop off takes away the lit
    # button and the rectangle and nothing else; the map lets go by itself once the
    # browse moves on past the group.
    state = replace(state, portrait_loop="") if which == 2 else replace(state, landscape_loop="")
    return state, [WindowOp(op="notice", key="Loop off", source=_satellite_source(which))]


_FILTER_TARGETS = {"both": (2, 3), "portrait": (2,), "landscape": (3,)}


def _side_filter(state: BridgeState, which: int) -> str:
    return state.portrait_filter if which == 2 else state.landscape_filter


def _rebuild_side(
    which: int, query: str, state: BridgeState, config: BridgeConfig
) -> SatelliteFilterFlowResult:
    """Rebuild one satellite's browse under *query* and its own current ordering.

    The single place a satellite's playlist is rebuilt from its sources, so a
    filter and a reorder cannot drift apart in how they read the side's state.
    """
    return apply_satellite_filter(
        which=which,
        query=query,
        f_mode_enabled=state.f_mode_enabled,
        recent=state.portrait_latest if which == 2 else state.landscape_latest,
        sources=config.portrait_sources if which == 2 else config.landscape_sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        cmd_file=config.satellite_cmd_file(which),
        provider_media_root=config.provider_media_root,
        provider_metadata_root=config.provider_metadata_root,
    )


def _dispatch_set_filter(
    scope: str, query: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Apply a metadata filter to one or both satellites and rebuild them.

    ``scope`` is both/portrait/landscape; ``query`` is the substring to match
    ("" clears).  Each targeted satellite records its own filter in the state so
    later F-mode / reorder rebuilds keep it, then reloads under its own ordering.
    """
    ops: list[WindowOp] = []
    for which in _FILTER_TARGETS[scope]:
        result = _rebuild_side(which, query, state, config)
        # Only remember a filter that actually selected videos: a zero-match
        # filter left the current playlist alone, so recording it would let the
        # next F-mode/reorder rebuild blank the satellite.  A filter that *did* rebuild
        # also replaced any loop's sub-playlist, so the loop (and its widened row)
        # is gone; a zero-match one touched nothing, so a running loop survives it.
        if result.applied:
            if which == 2:
                state = replace(state, portrait_filter=query)
            else:
                state = replace(state, landscape_filter=query)
            state = _clear_side_grouping(state, which)
        logger.info(result.log_message)
        # A filter that selected nothing left the playlist untouched — a dead end,
        # so it reads red like the other no-effect notices.
        level = NOTICE if result.applied else FAILED_NOTICE_LEVEL
        ops.append(WindowOp(op="notice", key=result.log_message, source=_satellite_source(which), level=level))
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
