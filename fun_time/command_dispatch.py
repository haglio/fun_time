"""Python-side command dispatcher for the Windows bridge.

Replaces the AHK ProcessDashboardCommand switch/case with a Python
implementation that calls logic modules directly as function calls
instead of subprocess invocations via the plan-file protocol.
"""
from __future__ import annotations

import functools
import logging
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from .audio_volume import MAX_VOLUME, MIN_VOLUME, VOLUME_STEP, publish_audio_level
from .config import RegenConfig
from .media_actions import ensure_in_favs, make_web_url_from_path, move_to_weird, remove_from_favs
from .media_metadata import (
    GroupIndex,
    action_group_members,
    action_label,
    cached_group_index,
    load_metadata,
    metadata_path_for,
    normalize_path_key,
    reject_action,
    reset_group_index_cache,
    seed_family_members,
    widened_seed_members,
)
from player_core.file_channel import append_command
from player_core.hud_status import F_MODE_LABEL, LATEST_LABEL, SHUFFLE_LABEL

from .dashboard_runtime import genau_enabled_path, read_genau_enabled, read_nau_status
from .lock import build_lock_plan
from .lock_hud import cell_path, hud_map_cells, locate_cell, navigate_cell
from .modes import collect_video_files, is_favorite_path, read_favs_content, write_playlist_file
from .random_favs_browser import FavEntry, target_for_fav
from .rfb_tab_page import tabs_dir, write_lock_tab_page
from .mode_plan import STARTUP_MAIN_MODE, genau_active, nau_displays
from .filter_vocab import decode_filter_command
from .omnipause import build_omnipause_plan
from .runtime_flow import (
    FMODE_PLAYERS,
    LANDSCAPE_PLAYER,
    PORTRAIT_PLAYER,
    MAIN_PLAYER,
    SatelliteFilterFlowResult,
    apply_enter_omnipause,
    apply_fmode,
    apply_leave_omnipause,
    apply_main_fmode,
    apply_mode_switch,
    apply_satellite_filter,
    satellite_browse_paths,
)
from .satellite_control import read_satellite_status, write_satellite_command
from .window_roles import visible_main_slot_roles
from .watch_stats import record_watch_event, watch_stats_path
from .event_log import (
    FAVORITE,
    NOTICE,
    SOURCE_LANDSCAPE,
    SOURCE_PORTRAIT,
    SOURCE_MAIN,
    SOURCE_SYSTEM,
)

logger = logging.getLogger(__name__)

# A notice that reports a command had no effect ("No other seeds") is logged at
# ERROR so the log panel and the on-player flash render it red, not white — the
# user asked to tell a command that did something from one that hit a dead end at
# a glance.
FAILED_NOTICE_LEVEL = logging.ERROR

# The other end of the same trick: a notice about the favorites — locking a clip
# into them, taking one back out, turning their filter on — is logged a level
# above NOTICE so it flashes green, which is what green means everywhere in this
# app.  Everything else a command announces is a plain white NOTICE.
FAVORITE_NOTICE_LEVEL = FAVORITE


def _satellite_source(which: int) -> str:
    """The event-log source for satellite slot *which* (2=portrait, 3=landscape)."""
    return SOURCE_PORTRAIT if which == 2 else SOURCE_LANDSCAPE


@dataclass
class BridgeState:
    locked2: bool = False
    locked3: bool = False
    main_mode: str = STARTUP_MAIN_MODE
    # Whether each player is in F-mode, held per player because it is set per
    # player: each HUD carries its own button, and only the bare "f mode" (and the
    # F key) still reaches all three at once.  It narrows the satellites to the
    # favorites and the main player to the videos that have a funscript, so which
    # player it is on genuinely changes what it means.
    main_f_mode: bool = False
    portrait_f_mode: bool = False
    landscape_f_mode: bool = False
    omni_paused: bool = False
    # Which browse order each player is in: newest-first ("Latest") when set,
    # else shuffled.  Per player, since Latest and Shuffle name one, and read by
    # every later rebuild (a filter, F-mode) so that player reloads the same way.
    main_latest: bool = False
    portrait_latest: bool = False
    landscape_latest: bool = False
    # The player most recently navigated (1=main/Nau, 2=portrait,
    # 3=landscape). Any portrait_/landscape_ command, or a main next/prev,
    # updates it; the side-agnostic "active_*" commands resolve against it —
    # nav (next/prev) reaches all three, the satellite-only actions only 2/3.
    # Starts on the main player: it is the display the eye opens on, so it holds the
    # floor until a satellite is addressed.
    active_side: int = 1
    # Per-satellite metadata filter queries ("" = no filter). Persisted in the shared
    # state file so they survive the dispatch loop's per-tick state resync and
    # are honored by later F-mode / reorder rebuilds.
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
    # The main player's sound level, 0-100, and whether it is silenced.  A
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
    main_sources: str
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
    # Our own interpreter — what the library browser is launched with, since the
    # bridge process has no Qt event loop to host that window in.
    python_exe: str = ""
    broker_cmd_file: Path | None = None
    broker_heartbeat_file: Path | None = None
    broker_tray_launcher: Path | None = None
    # Where the broker keeps the rest of its channel.  Unset it falls back to
    # ``state_dir``, which is what the two are for every session that runs from
    # the primary checkout; a branch session moves ``state_dir`` into its worktree
    # and this stays on the main player, because the broker is still the machine's one
    # broker.  See :attr:`fun_time.config.PathsConfig.broker_state_dir`.
    broker_state_dir: Path | None = None
    regen_media_root: Path | None = None
    regen_metadata_root: Path | None = None
    regen_generate_video_url: str = "https://example.com/video"
    regen_generate_image_url: str = "https://example.com/create"

    def satellite_cmd_file(self, which: int) -> Path:
        return self.portrait_cmd_file if which == 2 else self.landscape_cmd_file

    def satellite_status_file(self, which: int) -> Path:
        return self.portrait_status_file if which == 2 else self.landscape_status_file

    def satellite_playlist_file(self, which: int) -> Path:
        return self.portrait_playlist_file if which == 2 else self.landscape_playlist_file

    @property
    def broker_state(self) -> Path:
        """The directory the broker's files live in, defaulted to our own."""
        return self.broker_state_dir or self.state_dir

    @property
    def genau_enabled_file(self) -> Path:
        """Our switch for whether the broker may hand the OSR2 to Genau."""
        return genau_enabled_path(self.broker_state)

    @property
    def osr2_serial_rx_file(self) -> Path:
        """When the OSR2 last spoke, as the broker last stamped it."""
        return self.broker_state / "osr2_serial_rx.txt"

    @property
    def osr2_serial_tx_file(self) -> Path:
        """When a driver last spoke TO the OSR2.  The device only replies to
        traffic, so through a quiet stretch (an OmniPause, a handoff buffer) the
        RX stamp alone goes stale on a device that is on and in use — this one
        says somebody is still driving it."""
        return self.broker_state / "osr2_serial_tx.txt"

    @property
    def regen(self) -> RegenConfig:
        """The four Provider settings, in the shape the regenerate code expects."""
        return RegenConfig(
            generate_video_url=self.regen_generate_video_url,
            generate_image_url=self.regen_generate_image_url,
            media_root=self.regen_media_root,
            metadata_root=self.regen_metadata_root,
        )


@dataclass(frozen=True)
class WindowOp:
    op: str
    # The op's one payload: a role name, an RFB URL, or a notice's message.
    key: str = ""
    # Which window a ``notice`` op is about, so the log panel can filter it.
    source: str = SOURCE_SYSTEM
    # The log level a ``notice`` op is logged at — NOTICE (white) for a normal
    # confirmation, FAVORITE (green) for one about the favorites or a funscript,
    # ERROR (red) for a command that hit a dead end.
    level: int = NOTICE


_GENAU_CMD_MAP = {
    "genau_amplitude_down": "AMPLITUDE_DOWN",
    "genau_amplitude_up": "AMPLITUDE_UP",
    "genau_center_down": "CENTER_DOWN",
    "genau_center_up": "CENTER_UP",
    "genau_cycle_shape": "CYCLE_SHAPE",
    "genau_cycle_shape_prev": "CYCLE_SHAPE_PREV",
    # Cruise varies the stroke, never which clip plays — moving on from a clip is
    # what an unlocked Genau does by itself, at the pace below.
    "genau_toggle_cruise": "TOGGLE_CRUISE",
    "genau_cruise_on": "CRUISE_ON",
    "genau_cruise_off": "CRUISE_OFF",
    # How long an unlocked Genau leaves each clip on screen, a second at a time.
    # There is no switch to go with it: the padlock is the switch, and this is
    # only its pace (see _PRIMARY_LOCK_COMMANDS).  Named for what the number is —
    # a clip's seconds — rather than for the auto-advance that consumes it.
    "genau_clip_seconds_down": "CLIP_SECONDS_DOWN",
    "genau_clip_seconds_up": "CLIP_SECONDS_UP",
    # Condemning a clip outright — Genau's counterpart of a satellite's weird.
    "genau_weird_clip": "WEIRD",
    "genau_prev_clip": "PREV",
    "genau_next_clip": "NEXT",
    # The stroke's rate as the console's own ± marks beside the wave send it.
    # Genau's alone: the marks sit next to the drive readout and must move the
    # thing they sit next to, never the playback rate on the far side of the
    # panel.  The unqualified pair is _SPEED_BY_DRIVER below.
    "genau_speed_down": "SPEED_DOWN",
    "genau_speed_up": "SPEED_UP",
}


# Speed control splits by which control said it.  The console draws each engine
# its own ±: the stroke's rate on Genau's readout (see _GENAU_CMD_MAP) and the
# video's on Nau's playback row, and neither reaches across, because a labeled
# button has to move the thing it is labeled for.
# Spoken ("speed up", "slow down") or pressed on J/L there is no such label, so
# the bare pair follows whichever engine actually holds the OSR2 — Nau's video
# while its funscript is driving, since mpv's clock scales the script with it,
# and Genau's stroke otherwise.  Pinning it to Genau regardless nudged a paused
# engine whose own marks the console had already dimmed for exactly that reason,
# and nothing moved.
_SPEED_BY_DRIVER = {
    "speed_down": "SPEED_DOWN",
    "speed_up": "SPEED_UP",
}
# The video's own playback rate, as opposed to the stroke's — always Nau's.
_SPEED_NAU_RELATIVE = {
    "nau_speed_down": "SPEED_DOWN",
    "nau_speed_up": "SPEED_UP",
}
# An absolute video-speed set (min / max / a spoken multiplier) tunes whatever
# Nau is showing, so it lands even during a Genau-driven stretch; Genau has no
# multiplier, so that side is a no-op there.
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
    """Map a speed command to (nau_cmd, genau_cmd, by_driver), or None.

    ``by_driver`` marks the unqualified nudge, which follows whichever engine
    holds the OSR2; every other speed command names its engine.  A ``None`` side
    means that engine has no equivalent and ignores the command.
    """
    if command in _SPEED_BY_DRIVER:
        # Both engines answer the same verb, so which file it lands in is the
        # whole of the routing.
        keyword = _SPEED_BY_DRIVER[command]
        return keyword, keyword, True
    if command in _SPEED_NAU_RELATIVE:
        # The video playback rate — Nau's alone.  It reaches Nau in nau and hybrid
        # (where Nau is on screen) and is ignored in genau, where Genau's clips
        # have no such rate.
        return _SPEED_NAU_RELATIVE[command], None, False
    if command in _SPEED_EXTREMES:
        nau_cmd, genau_cmd = _SPEED_EXTREMES[command]
        return nau_cmd, genau_cmd, False
    nau_cmd = _parse_nau_speed(command)
    if nau_cmd is not None:
        return nau_cmd, None, False
    return None


def _speed_target(state: BridgeState, config: BridgeConfig, *, by_driver: bool) -> str:
    """Which engine a speed command drives.

    nau mode -> 'nau'; genau mode -> 'genau'.  Hybrid runs both, so an
    engine-named command goes where its name says — the video's rate to Nau, the
    one on screen — while the unqualified nudge follows the OSR2: Nau's funscript
    while it is driving, else Genau.  Genau is paused for the whole of a scripted
    stretch, so a nudge sent there then reaches an engine that cannot move.
    """
    if not genau_active(state.main_mode):
        return "nau"
    if not nau_displays(state.main_mode):
        return "genau"
    if not by_driver:
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
    "nau_end_compilation": "END_COMPILATION",
    "nau_full_vid": "PLAY_FULL_VID",
    "nau_clip_jump": "PLAY_CLIP_JUMP",
    # Funscript navigation: past this video's quiet stretch, or on to the next
    # video in the playlist that has a script at all (landing on its action).
    # Only Nau can answer either — it holds the playlist's funscript column and
    # the parsed script of what is playing.
    "nau_funscript_jump": "JUMP_TO_FUNSCRIPT",
    "nau_next_funscripted": "NEXT_FUNSCRIPTED",
}


_GENAU_NUMERIC_PREFIXES = {
    "genau_amp_": "AMP",
    "genau_center_": "CENTER",
    "genau_speed_": "SPEED",
    # Seconds a clip holds the screen, unlike the 0-100 axes above.
    "genau_clip_seconds_": "CLIP_SECONDS",
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
            config.regen,
        )
        if target.url:
            uri = write_lock_tab_page(tabs_dir(config.state_dir), target)
            lock_ops.append(WindowOp(op="open_rfb_tab", key=uri))
    # A lock does not end a group loop: it holds one clip (mpv ``loop_file``) and
    # leaves the loop's queue exactly as it was, so unlocking drops the side straight
    # back into cycling that group.  The loop therefore stays in state — a lock is a
    # pause at one position *inside* the loop, and the HUD goes on drawing the loop
    # (lit button, group rectangle, frozen map) with the held clip ringed.
    next_state = replace(state, locked2=plan.next_locked) if which == 2 else replace(state, locked3=plan.next_locked)
    return next_state, lock_ops


def _discard(
    which: int, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    locked = state.locked2 if which == 2 else state.locked3
    current_path = _satellite_current(config, which)
    # "Weird" judges the video the speaker saw.  When the satellite advanced
    # while the phrase was being recognized, jump back to the condemned clip
    # before trashing it, so the wrong (innocent) clip is never the one dropped.
    condemned = target_path or current_path
    already_moved_on = bool(target_path) and not _same_video(target_path, current_path)
    # Whether this is a demotion or a condemnation is read from the same favs
    # file that lights the HUD's ★ for this clip, so the key does what the badge
    # on screen implies: a starred clip loses the star, an unstarred one goes.
    is_favorite = is_favorite_path(condemned, read_favs_content(config.favs_file))
    plan = build_lock_plan(
        "discard", which=which, locked=locked, current_path=condemned, is_favorite=is_favorite
    )
    if locked:
        # A locked satellite is repeat-one; drop the lock so TRASH advances into
        # the playlist instead of looping the clip that replaced the discarded one.
        _send_satellite(config, which, "UNLOCK")
    if plan.remove_from_favs and condemned:
        remove_from_favs(config.favs_file, condemned)
    if plan.advance_playlist:
        if plan.drop_from_playlist:
            if already_moved_on:
                _play_video(config, which, condemned)
            # TRASH drops the current clip from the playlist and plays the next.
            _send_satellite(config, which, "TRASH")
        elif not already_moved_on:
            # A demotion leaves the clip in the playlist, so this is a plain
            # advance (NEXT) and PREV comes straight back to it.  Nothing has to
            # be done to the clip itself, so a satellite that already moved on is
            # left alone rather than dragged back to a clip it would leave again.
            _send_satellite(config, which, "NEXT")
    if plan.move_to_weird and condemned:
        move_to_weird(config.weird_dir, Path(condemned))
    if plan.log_message:
        logger.info(plan.log_message)
    # Which of the two things this key does is invisible otherwise: both look
    # like "the clip went away", and the ★ that tells them apart is gone by the
    # time you could read it.  The color says it too — undoing a favoriting is
    # green, condemning a clip that was never one is not.
    discard_ops = (
        [WindowOp(
            op="notice", key=plan.notice_message, source=_satellite_source(which),
            level=FAVORITE_NOTICE_LEVEL if plan.notice_about_favorites else NOTICE,
        )]
        if plan.notice_message
        else []
    )
    next_state = replace(state, locked2=False) if which == 2 else replace(state, locked3=False)
    return next_state, discard_ops


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
    meta_path = metadata_path_for(video_path, config.regen_metadata_root)
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
        metadata_root=config.regen_metadata_root,
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

# The main player's own reset — the same word, meaning for it what it means for a
# satellite: drop whatever is narrowing the playlist.  What narrows Nau is its
# length mode (and any compilation it is inside, which leaving the length mode
# leaves too) and its F-mode.  It is a command of ours rather than a bare forward
# to Nau because half of it is ours: the F-mode flag is the orchestrator's, set
# from three places of which Nau is only one.
MAIN_RESET = "main_reset"

# "no loop" ends a group loop but, unlike reset, keeps the satellite's filter.
_NO_LOOP_SIDES: dict[str, str] = {
    "portrait_no_loop": "portrait",
    "landscape_no_loop": "landscape",
}

# What a satellite's one loop key steps through, in order: the seed family, then
# the action group, then off — and round to the seed family again.  "" is the off
# stop, and is where a side that is not looping already stands, so the first press
# starts a seed loop.
_LOOP_CYCLE: tuple[str, ...] = ("seed", "action", "")

# The key command that walks a side around _LOOP_CYCLE — the loop's counterpart of
# the bare "<side>_lock" toggle, against the explicit <side>_seed_loop /
# <side>_action_loop / <side>_no_loop the voice commands reach.
_LOOP_CYCLE_SIDES: dict[str, int] = {
    "portrait_loop": 2,
    "landscape_loop": 3,
}

# "no filter" drops just the filter — the narrow counterpart of reset, which puts
# the whole side back to its defaults.
_NO_FILTER_SIDES: dict[str, str] = {
    "portrait_no_filter": "portrait",
    "landscape_no_filter": "landscape",
}

# A satellite's own minimize button (``satellite.hud.CONTROLS``), by the window
# role the dispatch loop resolves it to.  Every player's window here is
# borderless, so none of them carries a minimize box of its own, and the only
# other way to park one was the dashboard's minimize — which takes the whole room
# down together.
_MINIMIZE_ROLES: dict[str, str] = {
    "portrait_minimize": "portrait",
    "landscape_minimize": "landscape",
}

# The main player's own console button (``nau.console``).  It names the *slot*
# rather than a window, because two players share that rect.
MAIN_MINIMIZE = "main_minimize"


def _minimize_ops(command: str, main_mode: str) -> list[WindowOp] | None:
    """The windows *command* asks to have parked, or None when it asks for none.

    A satellite names its own window.  The main player names its slot, which Nau
    and Genau share — so its button parks whichever of the pair the mode has on
    screen, and never the one the mode has already put away: minimizing a hidden
    window is what drags it back into view.
    """
    role = _MINIMIZE_ROLES.get(command)
    if role is not None:
        return [WindowOp(op="minimize_role", key=role)]
    if command == MAIN_MINIMIZE:
        return [WindowOp(op="minimize_role", key=slot_role)
                for slot_role in visible_main_slot_roles(main_mode)]
    return None

# The two browse orderings, per player: Latest reloads newest-first, Shuffle
# reshuffles.  "both …" reaches each satellite in turn (the dispatch loop expands
# it), which is what the P key sends.  The main player is 1 and reloads through
# Nau rather than through a satellite rebuild, so it is dispatched separately
# below — the table only says which player and which order.
_REORDER_COMMANDS: dict[str, tuple[int, bool]] = {
    "main_latest": (1, True),
    "portrait_latest": (2, True),
    "landscape_latest": (3, True),
    "main_shuffle": (1, False),
    "portrait_shuffle": (2, False),
    "landscape_shuffle": (3, False),
}

# The same two orders as Genau answers to, keyed by ``recent``.  Every other
# player is handed a rewritten playlist file; Genau owns its own sequence and
# rescans its clips folder for itself, so its order crosses as a verb.
_GENAU_ORDER_CMD: dict[bool, str] = {True: "LATEST", False: "SHUFFLE"}


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
    shows.  The widen never leaves the clip's own action, so the dead end here is
    "nothing else in the library does this act", which is a real answer rather
    than a reason to hand back some other act."""
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


# "Wrong action" — the clip is fine, its label is not.  Per side, like every
# other judgement of the clip on screen.
_WRONG_ACTION_SIDES: dict[str, int] = {"portrait_wrong_action": 2, "landscape_wrong_action": 3}


def _dispatch_wrong_action(
    which: int, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Strike the act out of the current clip's sidecar — "wrong action".

    Nothing about playback changes: the clip is not bad, only mislabeled, so it
    plays on.  What changes is the library: with no ``video.action`` the clip
    reads as still needing one, which is what brings it back around in Evolver's
    backfill tool to be named again.

    The clip judged is the one the speaker was looking at (*target_path*) rather
    than whatever an auto-advancing satellite has moved on to, exactly as for
    "weird" and the cycles.
    """
    source = _satellite_source(which)
    current = target_path or _satellite_current(config, which)
    if not current:
        return state, []
    action = reject_action(current, config.regen_metadata_root)
    if not action:
        return state, [WindowOp(
            op="notice", key="No action to remove", source=source, level=FAILED_NOTICE_LEVEL
        )]
    # The grouping index carries the act it just lost — it decides the HUD's
    # action column, its labels and where a cycle goes next — so it has to be
    # rebuilt rather than left describing the sidecar as it was.
    reset_group_index_cache()
    logger.info("Wrong action on %s: removed %r", current, action)
    return state, [WindowOp(op="notice", key=f"Action removed: {action}", source=source)]


def _loop_members(
    which: int, axis: str, state: BridgeState, config: BridgeConfig, current: str
) -> tuple[list[str], bool]:
    """The clips *axis*'s loop would run on satellite *which* around *current*, and
    whether that pool is the widened seed row rather than the exact family.

    Fewer than two members means the group holds only this clip, so there is no loop
    to be had on that axis — which is what turns the loop into a lock below and what
    makes the loop key step past the axis.  The group index behind this is cached,
    so asking a second time before dispatching costs nothing.
    """
    index = _satellite_group_index(which, config, current)
    # Loop what the HUD is showing: if the seed row has been widened around this
    # very clip ("more seeds"), loop that wider pool, not just the exact family.
    widen_clip = state.portrait_widen_clip if which == 2 else state.landscape_widen_clip
    widened = axis == "seed" and normalize_path_key(widen_clip) == normalize_path_key(current)
    gather = widened_seed_members if widened else (
        action_group_members if axis == "action" else seed_family_members
    )
    return [member for member in gather(index, current) if Path(member).exists()], widened


def _dispatch_group_loop(
    which: int, axis: str, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Loop the satellite around the current clip's action group or seed family."""
    source = _satellite_source(which)
    ops: list[WindowOp] = []
    current = target_path or _satellite_current(config, which)
    if not current:
        return state, ops
    members, widened = _loop_members(which, axis, state, config, current)
    if len(members) < 2:
        # Only this clip is in the group, so "looping" it is a single-video lock:
        # LOCK this one.  Never a dead end — the loop buttons are still valid with
        # one video, they just mean "lock" then.  A lock is not a loop, so any
        # prior loop (and widened row) is dropped.
        _send_satellite(config, which, "LOCK")
        state = replace(state, locked2=True) if which == 2 else replace(state, locked3=True)
        state = _clear_side_grouping(state, which)
        # Green: locking a clip puts it in the favorites, so it says so in the
        # color the favorites own.
        return state, [WindowOp(op="notice", key="Locked", source=source,
                                level=FAVORITE_NOTICE_LEVEL)]
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


def _dispatch_loop_cycle(
    which: int, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Step a satellite's loop one place around :data:`_LOOP_CYCLE`.

    Where the step starts is read off the loop the side is actually running — the
    same flag the HUD lights its loop button from — so the key and the HUD can never
    disagree about where in the cycle the side stands.

    An axis whose group holds only this clip has no loop to offer, and is stepped
    over rather than landed on.  Without that, a clip nobody re-seeded would answer
    every press with the same single-video lock (which is what a group of one makes
    a loop mean) and its action loop would be unreachable from the keyboard.

    When neither axis can loop, that lock is the only thing a press can say, so the
    cycle collapses to it — never through to an "off" that is already off, which
    would rebuild the browse for nothing.  There the key is a two-stop cycle: lock
    the clip, then let it go again.  A one-stop cycle would be a trap, holding a
    clip the only key on it could no longer release.
    """
    current = target_path or _satellite_current(config, which)
    if not current:
        return state, []
    running = _side_loop(state, which)
    # An unknown flag (a hand-edited state file) reads as "not looping", so the
    # cycle starts over at its first axis rather than raising.
    start = _LOOP_CYCLE.index(running) + 1 if running in _LOOP_CYCLE else 0
    for step in range(len(_LOOP_CYCLE)):
        axis = _LOOP_CYCLE[(start + step) % len(_LOOP_CYCLE)]
        if not axis:
            if running:
                return _dispatch_no_loop("portrait" if which == 2 else "landscape", state, config)
            continue  # nothing is looping, so the off step has nothing to switch off
        if len(_loop_members(which, axis, state, config, current)[0]) >= 2:
            return _dispatch_group_loop(which, axis, state, config, current)
    if state.locked2 if which == 2 else state.locked3:
        state = _cancel_lock(which, state, config)
        return state, [WindowOp(op="notice", key="Unlocked", source=_satellite_source(which))]
    # The lone-clip loop's own lock, so the press means exactly what "loop seeds"
    # would have meant on this clip.
    return _dispatch_group_loop(which, _LOOP_CYCLE[0], state, config, current)


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
# column) and switches the satellite to the picked cell.  Enter used to lock the
# selection and re-home the map on it; the side's own lock key already does
# both, so that command was retired rather than kept as a second way in.
_NAV_DIRECTIONS = ("left", "right", "up", "down")


def _parse_nav(command: str) -> tuple[int, str] | None:
    """``(slot, direction)`` for a ``<side>_nav_<dir>`` command, else None."""
    for prefix, which in (("portrait_nav_", 2), ("landscape_nav_", 3)):
        if command.startswith(prefix):
            direction = command[len(prefix):]
            if direction in _NAV_DIRECTIONS:
                return which, direction
    return None


def _is_hud_nav_command(command: str) -> bool:
    """Whether *command* drives HUD keyboard navigation — a direction step.  Those
    manage the side's nav anchor themselves, so the generic "any other side
    command re-homes the map" rule leaves them alone."""
    return _parse_nav(command) is not None


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
    anchor; only a step with genuinely nowhere to go — sideways off the action
    column, or along an axis holding just the anchor — is a dead end, reported
    red like the other no-effect notices.

    A vertical step from a seed cell dives into THAT seed's own action column —
    the one the HUD draws under the lit cell (``build_hud_panel`` hangs it
    there) — re-rooting the map on the seed it stepped down from.  The frozen
    anchor's own acts belong to the corner's seed, which is a different clip.
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
    root = anchor
    seeds, actions = hud_map_cells(index, root)
    cell = locate_cell(current, root, seeds, actions) or ("corner", 0)
    if cell[0] == "seed" and direction in ("down", "up"):
        # Dive into the lit seed's own column: step as if from the corner of the
        # map homed on it.  Committed below only if the step actually lands, so a
        # seed with no other acts stays a dead end on an unmoved map.
        root, cell = current, ("corner", 0)
        seeds, actions = hud_map_cells(index, root)
    target_cell = navigate_cell(cell, direction, seed_count=len(seeds), action_count=len(actions))
    target = cell_path(target_cell, root, seeds, actions)
    if target_cell == cell or not target or _same_video(target, current):
        state = _set_nav_anchor(state, which, anchor)
        return state, [WindowOp(op="notice", key="No clip that way", source=source, level=FAILED_NOTICE_LEVEL)]
    state = _set_nav_anchor(state, which, root)
    return _dispatch_play_video(which, target, state, config)


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


# The dispatcher counts players in slots; everything that *draws* them names them
# instead (the HUD panels, the publisher's filenames, each player's own side).  This
# is the one crossing between the two, so a slot never reaches a player as a number.
MAIN_SIDE = 1
SIDE_NAMES = {MAIN_SIDE: "main", 2: "portrait", 3: "landscape"}

# The main slot's lock: repeat what is on screen, or let it move on — Nau's
# video into the next playlist entry, Genau's clip into the next clip after its
# interval.  Both players answer these three verbs and both open locked, so the
# one padlock on the console means the same thing whichever is showing; the mode
# decides which of them hears it, exactly as it decides for prev/next.  The toggle
# is the key and the button; the absolute pair is what the spoken forms send,
# since a speaker asks for the state they want.
_PRIMARY_LOCK_COMMANDS = {
    "main_lock": "TOGGLE_LOCK",
    "main_lock_on": "LOCK_ON",
    "main_lock_off": "LOCK_OFF",
}

# What makes the main player the one a later bare command reaches: navigating it,
# locking it, or naming its F-mode.  The satellites' own keys select a side the
# same way, and every ``portrait_``/``landscape_`` command does it by prefix — so
# without the F-mode forms here, "main f mode" would be the one way of
# addressing a player that did not leave it addressed.
_PRIMARY_SELECTING_COMMANDS = frozenset(
    {"main_next", "main_prev", MAIN_RESET}
    | set(_PRIMARY_LOCK_COMMANDS)
    | {f"main_fmode{suffix}" for suffix in ("", "_on", "_off")}
)


def side_name(slot: int) -> str:
    """The name of the player in *slot*, or "" for no player — the inverse of
    :func:`command_side`'s numbering."""
    return SIDE_NAMES.get(slot, "")


def command_side(command: str) -> int | None:
    """The player slot a command addresses: 1=main, 2=portrait, 3=landscape —
    or None if it addresses no player.  :data:`SIDE_NAMES` is the inverse.

    The main (Nau) player is selected by its own next/prev navigation, by its
    lock, and by naming its F-mode or its reset — everything it shares with a
    satellite.  It has no weird/cycle, so nothing else selects it.
    """
    if command.startswith("portrait_"):
        return 2
    if command.startswith("landscape_"):
        return 3
    if command in _PRIMARY_SELECTING_COMMANDS:
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
    that is *about a particular video* honors it: lock, weird, wrong-action,
    cycle, the group loops and lock-action.  Navigation is relative rather than
    video-scoped, and the rest of the vocabulary names no video at all, so both
    ignore it.  Empty means "whatever is playing now", which is how every
    keyboard and dashboard command arrives.
    """
    ops: list[WindowOp] = []

    # Parking a player, before the active-side bookkeeping below: this is the one
    # side command that is not about that side's video, and a player just taken
    # off the screen must not become the one a bare "lock" or "next" reaches —
    # that would send the next spoken word to a window nobody can see.
    minimize_ops = _minimize_ops(command, state.main_mode)
    if minimize_ops is not None:
        return state, minimize_ops

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
    # and switches the satellite to it.
    nav = _parse_nav(command)
    if nav is not None:
        return _navigate_hud(*nav, state, config)

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

    wrong_action_side = _WRONG_ACTION_SIDES.get(command)
    if wrong_action_side is not None:
        return _dispatch_wrong_action(wrong_action_side, state, config, target_path)

    loop_target = _LOOP_COMMANDS.get(command)
    if loop_target is not None:
        which, axis = loop_target
        return _dispatch_group_loop(which, axis, state, config, target_path)

    loop_cycle_side = _LOOP_CYCLE_SIDES.get(command)
    if loop_cycle_side is not None:
        return _dispatch_loop_cycle(loop_cycle_side, state, config, target_path)

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
        state, discard_ops = _discard(2, state, config, target_path)
        ops.extend(discard_ops)
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
        state, discard_ops = _discard(3, state, config, target_path)
        ops.extend(discard_ops)
        return state, ops

    if command in ("main_prev", "main_next"):
        # Nau owns the main player in nau and hybrid; in genau mode the
        # paused Nau still navigates in the background.
        append_command(
            config.nau_cmd_file, "PREV" if command == "main_prev" else "NEXT")
        return state, ops

    lock_verb = _PRIMARY_LOCK_COMMANDS.get(command)
    if lock_verb is not None:
        # To whichever player is showing, because the lock is about what is on
        # screen: Nau's video in nau and hybrid, Genau's clip in genau.  The same
        # split the speed controls make, and for the same reason.
        target = (config.nau_cmd_file if nau_displays(state.main_mode)
                  else config.genau_cmd_file)
        append_command(target, lock_verb)
        return state, ops

    if command in ("main_nudge_prev", "main_nudge_next"):
        # Nau owns the main player; its SEEK commands apply to a live local
        # clock, so rapid nudges stack naturally.
        append_command(
            config.nau_cmd_file,
            "SEEK_BACK" if command == "main_nudge_prev" else "SEEK_FWD")
        return state, ops

    if command == "projection_cycle":
        # FunTimeVR's main player answers this by walking flat → 180 → fisheye →
        # MKX200 → 360 and remembering the pick in the video's sidecar.  Routed
        # like every main-player verb so the desktop Nau simply logs it as unknown.
        if nau_displays(state.main_mode):
            append_command(config.nau_cmd_file, "CYCLE_PROJECTION")
        return state, ops

    if command == "recenter_view":
        # FunTimeVR re-zeroes its scene onto wherever the headset faces at
        # this instant; the runtime's own recenter UI never reaches the app.
        # Routed like every main-player verb so the desktop Nau logs it as unknown.
        if nau_displays(state.main_mode):
            append_command(config.nau_cmd_file, "RECENTER")
        return state, ops

    if command in _NAU_CMD_MAP:
        # Loop recording, versions and length only make sense while Nau owns the
        # main slot — nau and hybrid, but not genau.
        if nau_displays(state.main_mode):
            append_command(config.nau_cmd_file, _NAU_CMD_MAP[command])
        return state, ops

    if command in _MUTE_COMMANDS:
        return _dispatch_audio(replace(state, muted=_MUTE_COMMANDS[command]), config)

    step = _VOLUME_STEPS.get(command)
    if step is not None:
        return _dispatch_audio(_step_volume(state, step), config)

    if command.startswith(f"{SET_VOLUME_COMMAND}|"):
        return _dispatch_set_volume(command.partition("|")[2], state, config)

    if command == "quarter_button":
        append_command(config.genau_cmd_file, "OFFSET_QUARTER_CYCLE")
        return state, ops

    if command == "omnipause_toggle":
        return _dispatch_omnipause_toggle(state, config)

    if command == "enter_omnipause":
        return _dispatch_enter_omnipause(state, config)

    if command == "relief_omnipause":
        return _dispatch_enter_omnipause(state, config, relief=True)

    if command == "leave_omnipause":
        return _dispatch_leave_omnipause(state, config)

    fmode_target = _FMODE_COMMANDS.get(command)
    if fmode_target is not None:
        players, target = fmode_target
        return _dispatch_fmode(players, target, state, config)

    reorder = _REORDER_COMMANDS.get(command)
    if reorder is not None:
        which, recent = reorder
        if which == MAIN_SIDE:
            return _dispatch_main_reorder(recent, state, config)
        return _dispatch_reorder(which, recent, state, config)

    reset_scope = _RESET_SIDES.get(command)
    if reset_scope is not None:
        return _dispatch_reset(reset_scope, state, config)

    if command == MAIN_RESET:
        return _dispatch_main_reset(state, config)

    no_filter_scope = _NO_FILTER_SIDES.get(command)
    if no_filter_scope is not None:
        return _dispatch_set_filter(no_filter_scope, "", state, config)

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
        _toggle_genau_enabled(config.genau_enabled_file)
        return state, ops

    speed = _speed_engine_commands(command)
    if speed is not None:
        nau_cmd, genau_cmd, by_driver = speed
        target = _speed_target(state, config, by_driver=by_driver)
        if target == "nau" and nau_cmd is not None:
            append_command(config.nau_cmd_file, nau_cmd)
        elif target == "genau" and genau_cmd is not None:
            append_command(config.genau_cmd_file, genau_cmd)
        return state, ops

    if command in _GENAU_CMD_MAP:
        if genau_active(state.main_mode):
            append_command(config.genau_cmd_file, _GENAU_CMD_MAP[command])
        return state, ops

    genau_numeric = _parse_genau_numeric_command(command)
    if genau_numeric is not None:
        if genau_active(state.main_mode):
            append_command(config.genau_cmd_file, genau_numeric)
        return state, ops

    if command == "clipper_save":
        if state.main_mode != "genau":
            msg = _dispatch_clipper_save(config)
            if msg:
                ops.append(WindowOp(op="notice", key=msg, source=SOURCE_MAIN))
        return state, ops

    return state, ops


_VOLUME_STEPS = {"audio_volume_down": -VOLUME_STEP, "audio_volume_up": VOLUME_STEP}

# ``audio_set_volume|<0-100>`` — an absolute level, which is what a slider asks
# for where every other audio command asks for a step or a state.  Nau's volume
# control is the one that sends it.
SET_VOLUME_COMMAND = "audio_set_volume"


def _dispatch_set_volume(
    argument: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Set the sound level outright, lifting any mute.

    The level crosses a text channel from another process, so it is not trusted:
    out of range is clamped and unparseable is ignored, rather than raising into
    the dispatch loop over a dragged slider.  It lifts a mute for the reason a
    step does — reaching for the volume is asking to hear something.
    """
    try:
        level = int(argument)
    except ValueError:
        return state, []
    volume = max(MIN_VOLUME, min(MAX_VOLUME, level))
    return _dispatch_audio(replace(state, volume=volume, muted=False), config)

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
    """Publish *state*'s sound level to both of the main player's audio sinks,
    and say on screen what it now is."""
    publish_audio_level(
        nau_cmd_file=config.nau_cmd_file,
        genau_cmd_file=config.genau_cmd_file,
        audio_volume_file=config.audio_volume_file,
        volume=state.volume,
        muted=state.muted,
    )
    message = "Muted" if state.muted else f"Volume {state.volume}%"
    return state, [WindowOp(op="notice", key=message, source=SOURCE_MAIN)]


def _dispatch_omnipause_toggle(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Esc: whichever of enter and leave the current state calls for."""
    plan = build_omnipause_plan(
        "toggle",
        omni_paused=state.omni_paused,
        main_mode=state.main_mode,
    )
    if plan.action == "enter":
        return _dispatch_enter_omnipause(state, config)
    return _dispatch_leave_omnipause(state, config)


def _dispatch_enter_omnipause(
    state: BridgeState, config: BridgeConfig, *, relief: bool = False
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_enter_omnipause(
        omni_paused=state.omni_paused,
        main_mode=state.main_mode,
        portrait_paused_file=config.portrait_paused_file,
        landscape_paused_file=config.landscape_paused_file,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        nau_paused_file=config.nau_paused_file,
        broker_cmd_file=config.broker_cmd_file,
        relief=relief,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    ops = [WindowOp(op="disable_all_topmost"), WindowOp(op="suspend_hotkeys")]
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _dispatch_leave_omnipause(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    result = apply_leave_omnipause(
        omni_paused=state.omni_paused,
        main_mode=state.main_mode,
        portrait_paused_file=config.portrait_paused_file,
        landscape_paused_file=config.landscape_paused_file,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        nau_paused_file=config.nau_paused_file,
        broker_cmd_file=config.broker_cmd_file,
    )
    state = replace(state, omni_paused=result.next_omni_paused)
    # Un-minimize first, then re-band, then focus: leaving OmniPause is the room
    # coming back, so a player put away with its own minimize button comes back
    # with it — it has no panel left to ask with, its HUD having gone down with the
    # window.  Before the bands, so the re-stack and the activate below land on
    # windows that are actually on screen.
    ops = [WindowOp(op="restore_parked"), WindowOp(op="restore_all_topmost"),
           WindowOp(op="unsuspend_hotkeys")]
    ops.extend(_main_focus_ops(state.main_mode))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


def _main_focus_ops(main_mode: str) -> list[WindowOp]:
    """Re-activate the window that owns the main player (omnipause leave)."""
    role = "genau" if genau_active(main_mode) else "nau"
    return [WindowOp(op="activate_role", key=role)]


def _main_slot_ops(main_mode: str) -> list[WindowOp]:
    """Visibility + z-order ops for the main player-slot windows on a mode switch.

    The two players (Nau and Genau) share one screen rect; exactly the mode's
    player(s) are shown and the inactive slot-mate hidden.  The new window is
    shown and activated BEFORE the old one hides so focus never falls through
    to another application.  Finally the pair is re-stacked for the new mode
    (``restack_main``): Nau topmost, with Genau's HUD above it in hybrid.
    Nau and Genau overlap, so their z-order is explicit — unlike every other
    window, a plain topmost flag can't say "Genau above Nau, both on top."
    """
    restack = WindowOp(op="restack_main")
    if main_mode == "genau":
        return [
            WindowOp(op="show_role", key="genau"),
            WindowOp(op="activate_role", key="genau"),
            WindowOp(op="hide_role", key="nau"),
            restack,
        ]
    if main_mode == "hybrid":
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


# Which players each F-mode command reaches, and what it sets them to — None for
# the toggles, True/False for the forms that assert a state and so cannot land on
# the opposite of what was asked when a phrase is misheard twice.  The bare
# command — the F key, the spoken "f mode" — still means all three at once; naming
# a player reaches that one alone, off its own HUD button or its own spoken
# phrase.  ``both_fmode`` never arrives here: the dispatch loop expands every
# both_* into its portrait/landscape pair first.
_FMODE_COMMANDS: dict[str, tuple[tuple[str, ...], bool | None]] = {
    "fmode_toggle": (FMODE_PLAYERS, None),
    "fmode_on": (FMODE_PLAYERS, True),
    "fmode_off": (FMODE_PLAYERS, False),
    "main_fmode": ((MAIN_PLAYER,), None),
    "main_fmode_on": ((MAIN_PLAYER,), True),
    "main_fmode_off": ((MAIN_PLAYER,), False),
    "portrait_fmode": ((PORTRAIT_PLAYER,), None),
    "portrait_fmode_on": ((PORTRAIT_PLAYER,), True),
    "portrait_fmode_off": ((PORTRAIT_PLAYER,), False),
    "landscape_fmode": ((LANDSCAPE_PLAYER,), None),
    "landscape_fmode_on": ((LANDSCAPE_PLAYER,), True),
    "landscape_fmode_off": ((LANDSCAPE_PLAYER,), False),
}

# Where each player's flash goes, so a sided F-mode reports on that player's own
# display and the all-players one reports to the room.
_FMODE_NOTICE_SOURCE = {
    MAIN_PLAYER: SOURCE_MAIN,
    PORTRAIT_PLAYER: SOURCE_PORTRAIT,
    LANDSCAPE_PLAYER: SOURCE_LANDSCAPE,
}

_FMODE_STATE_FIELD = {
    MAIN_PLAYER: "main_f_mode",
    PORTRAIT_PLAYER: "portrait_f_mode",
    LANDSCAPE_PLAYER: "landscape_f_mode",
}


def _player_f_mode(state: BridgeState, player: str) -> bool:
    """Whether *player* is in F-mode — the one reader of the per-player flags."""
    return bool(getattr(state, _FMODE_STATE_FIELD[player]))


def _next_f_mode(state: BridgeState, players: tuple[str, ...]) -> bool:
    """What a toggle over *players* should set them all to.

    One player is an ordinary flip.  Several — the F key, or a spoken "f mode" —
    turn on unless every one of them is already on, so the key that means "narrow
    everything" can never leave half the room narrowed and half not: it either
    completes the narrowing or lifts it.
    """
    return not all(_player_f_mode(state, player) for player in players)


def _dispatch_fmode(
    players: tuple[str, ...], target: bool | None,
    state: BridgeState, config: BridgeConfig,
) -> tuple[BridgeState, list[WindowOp]]:
    """Put *players* into F-mode or out of it, rebuilding only what moves.

    *target* is the state to assert, or None to toggle.  A player already in the
    asked-for state is left out of the rebuild entirely: its playlist file is not
    rewritten, so "portrait f mode on" said twice does not reshuffle the queue the
    first one built.
    """
    enabled = _next_f_mode(state, players) if target is None else target
    changed = tuple(player for player in players if _player_f_mode(state, player) != enabled)
    result = apply_fmode(
        players=changed,
        enabled=enabled,
        main_recent=state.main_latest,
        portrait_recent=state.portrait_latest,
        landscape_recent=state.landscape_latest,
        main_sources=config.main_sources,
        portrait_sources=config.portrait_sources,
        landscape_sources=config.landscape_sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        portrait_cmd_file=config.portrait_cmd_file,
        landscape_cmd_file=config.landscape_cmd_file,
        nau_cmd_file=config.nau_cmd_file,
        regen_metadata_root=config.regen_metadata_root,
        portrait_filter=state.portrait_filter,
        landscape_filter=state.landscape_filter,
    )
    if result.players:
        logger.info(result.log_message)
    state = replace(state, **{_FMODE_STATE_FIELD[player]: enabled for player in result.players})
    # A rebuilt satellite got a new queue, which drops its lock, its group loop and
    # the widened seed row that rode on the loop — the same as any other rebuild.
    for player, which in ((PORTRAIT_PLAYER, 2), (LANDSCAPE_PLAYER, 3)):
        if player in result.players:
            state = _cancel_lock(which, state, config)
            state = _clear_side_grouping(state, which)
    # Flash which way it went, on the display it went on — a sided F-mode reports
    # from that player, the all-players one from the room.  It goes off the players
    # *asked for*, not the ones that moved: "f mode" is a gesture at the room even
    # on the press where only one player was left to narrow.  The dispatch owns
    # this rather than the voice echo, so the F key and the HUD buttons flash it
    # too, not just a spoken "F mode" (which is why fmode is self-reporting — see
    # SELF_REPORTING_COMMANDS).  Enabling is green, since what it narrows to is the
    # favorites and the funscripts; disabling is the loud one — the library just
    # came back, so it flashes red the way the other "this is now off" notices do.
    source = _FMODE_NOTICE_SOURCE[players[0]] if len(players) == 1 else SOURCE_SYSTEM
    notice_op = WindowOp(
        op="notice",
        key=f"{F_MODE_LABEL} enabled" if enabled else f"{F_MODE_LABEL} disabled",
        source=source,
        level=FAVORITE_NOTICE_LEVEL if enabled else FAILED_NOTICE_LEVEL,
    )
    return state, [notice_op]


def _dispatch_main_reorder(
    recent: bool, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Reload the main player in a fresh order — Latest (newest-first) or Shuffle.

    To whichever player owns the main slot's screen, the split the lock makes (see
    ``_PRIMARY_LOCK_COMMANDS``) and for the same reason: a browse order is about
    what you are looking at.  Sent to Nau regardless, "main latest" said in genau
    mode rewrote a playlist for a player that was neither on screen nor playing,
    and Genau — the one actually showing — went on with the order it launched in.

    Both branches rescan as they go, which is most of what "latest" is for: a clip
    that arrived since is in no list until something looks again.

    Nau's playlist is ours to write, so that branch rewrites the file and hands
    Nau the same RELOAD_PLAYLIST an F-mode change gets, from the top of the new
    order — a reorder filters nothing out, so Nau would otherwise keep the video
    on screen and carry on from wherever it now sits, the newest-first list
    applying only behind it and the arrivals never coming up.  Genau has no
    playlist file at all; it owns its own sequence, so it is told the order and
    rescans its clips folder itself.

    Nau's order alone is remembered, because ``main_latest`` describes the
    playlist file we built: a later F-mode rebuild reads it to reload the same way
    round, and the console draws it while Nau is the player showing.  Recording a
    Genau reorder there would light "Latest" over a Nau playlist nobody reordered.
    """
    on_nau = nau_displays(state.main_mode)
    if on_nau:
        state = replace(state, main_latest=recent)
        apply_main_fmode(
            enabled=state.main_f_mode,
            main_sources=config.main_sources,
            recent=recent,
            state_dir=config.state_dir,
            nau_cmd_file=config.nau_cmd_file,
            start_at_top=True,
        )
    else:
        append_command(config.genau_cmd_file, _GENAU_ORDER_CMD[recent])
    # The order's own word alone, and self-reported — see _dispatch_reorder.
    label = LATEST_LABEL if recent else SHUFFLE_LABEL
    logger.info("%s: main player (%s)", label, "nau" if on_nau else "genau")
    return state, [WindowOp(op="notice", key=label, source=SOURCE_MAIN)]


def _dispatch_main_reset(
    state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Put the main player back to its defaults — the satellites' reset, over here.

    Two things narrow what Nau plays, and both go: F-mode, whose playlist is
    rebuilt wide again, and the length mode, back to mixed — which leaves any
    compilation with it, since a compilation is a playing set the length mode was
    feeding.  The length verb is Nau's, so it is only sent while Nau owns the main
    slot; the F-mode flag is ours and is cleared whoever is showing, exactly as
    "main f mode off" clears it.

    The playlist is only rebuilt when F-mode was actually on.  A reset has never
    reshuffled the main player — "shuffle main" is the command that does — so a
    reset pressed with nothing narrowed must not throw away the browse either.
    """
    if state.main_f_mode:
        state = replace(state, main_f_mode=False)
        apply_main_fmode(
            enabled=False,
            main_sources=config.main_sources,
            recent=state.main_latest,
            state_dir=config.state_dir,
            nau_cmd_file=config.nau_cmd_file,
        )
    if nau_displays(state.main_mode):
        append_command(config.nau_cmd_file, _NAU_CMD_MAP["nau_length_mixed"])
    logger.info("Reset main player")
    return state, [WindowOp(op="notice", key="Reset", source=SOURCE_MAIN)]


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
    # From the top of the new order: asking for the latest is asking to see what has
    # just arrived, and the reload alone would leave the clip on screen playing with
    # the new order applying only behind it.
    result = _rebuild_side(which, _side_filter(state, which), state, config, start_at_top=True)
    state = replace(state, locked2=False) if which == 2 else replace(state, locked3=False)
    state = _clear_side_grouping(state, which)
    side = "portrait" if which == 2 else "landscape"
    # The order's own word and nothing else.  The toast flashes on the player it
    # was said to, and this is what that player's HUD calls the order it is now
    # in, so naming the player and then spelling the order out a second time
    # ("Latest: portrait newest-first") only read as a log line that had escaped
    # onto the screen.  The count and the side stay in the log, where they are of
    # use.  The dispatch owns the toast the way it owns F-mode's, so a spoken
    # reorder is not echoed on top of it (see SELF_REPORTING_COMMANDS).
    label = LATEST_LABEL if recent else SHUFFLE_LABEL
    logger.info("%s: %s (%d clips)", label, side, result.count)
    return state, [WindowOp(op="notice", key=label, source=_satellite_source(which))]


# Everything a reset takes off a satellite, as the ``BridgeState`` field holding
# it — portrait's, then landscape's.  The "is it already reset?" test reads this
# same list, so a narrowing the reset clears cannot be one the test forgets: that
# side would read as untouched and the press would do nothing while there was
# something to undo.
#
# The nav anchor is not here even though a reset drops it, because it is already
# gone: every side command that is not itself a nav step clears it on the way in
# (see :func:`dispatch_command`), so no reset has ever seen one set.
_RESET_STATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("locked2", "locked3"),
    ("portrait_filter", "landscape_filter"),
    ("portrait_f_mode", "landscape_f_mode"),
    ("portrait_latest", "landscape_latest"),
    ("portrait_loop", "landscape_loop"),
    ("portrait_map_anchor", "landscape_map_anchor"),
    ("portrait_widen_clip", "landscape_widen_clip"),
)


def _side_is_at_defaults(state: BridgeState, which: int) -> bool:
    """Whether satellite *which* already sits exactly where a reset would put it.

    Every one of those defaults is the empty value of its field — unlocked, no
    filter, no F-mode, shuffled rather than newest-first, no loop, no map anchor,
    no widened row — so "at its defaults" is "nothing in the list is set".
    """
    return not any(getattr(state, fields[0 if which == 2 else 1])
                   for fields in _RESET_STATE_FIELDS)


def _dispatch_reset(
    scope: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Put a satellite (or both) back to every default, not just its filter.

    The lock is released, the filter cleared, F-mode dropped, the order returned to
    shuffled, any group loop and widened row and frozen map dropped, and the side
    starts from the top of a freshly-built browse — one clip per subject.  "no
    filter" is the narrow gesture; this one is "put it back how it started".

    F-mode is one of those defaults, and is cleared here rather than left standing:
    a side reset while it was narrowed to the favorites came back still narrowed to
    them, which is a browse of a few dozen clips wearing the label of the whole
    library.  The flag goes into the state before the rebuild, because the rebuild
    reads it back to decide how wide to build.

    A side already sitting at those defaults is left alone entirely.  The rebuild
    reshuffles and starts at the top, so a second press landed on a different clip
    than the first, and a third on another again: a button that says "put it back"
    was the fastest way to keep changing what was playing.  Nothing is narrowed by
    then, so there is nothing the press could put back.
    """
    ops: list[WindowOp] = []
    for which in _FILTER_TARGETS[scope]:
        if _side_is_at_defaults(state, which):
            logger.info("Reset %s: already at its defaults", _satellite_source(which))
            continue
        state = _cancel_lock(which, state, config)
        state = _set_side_latest(state, which, False)
        state = _set_side_filter(state, which, "")
        state = _set_side_f_mode(state, which, False)
        state = _clear_side_grouping(state, which)
        state = _clear_nav_anchor(state, which)
        result = _rebuild_side(which, "", state, config, start_at_top=True)
        logger.info("Reset %s: %s", _satellite_source(which), result.log_message)
        ops.append(WindowOp(op="notice", key="Reset", source=_satellite_source(which)))
    return state, ops


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
    restored browse still honors it.
    """
    which = 2 if scope == "portrait" else 3
    current = _satellite_current(config, which)
    browse = satellite_browse_paths(
        which=which,
        query=_side_filter(state, which),
        f_mode_enabled=_side_f_mode(state, which),
        recent=state.portrait_latest if which == 2 else state.landscape_latest,
        sources=config.portrait_sources if which == 2 else config.landscape_sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        regen_metadata_root=config.regen_metadata_root,
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


def _side_loop(state: BridgeState, which: int) -> str:
    """Which group loop satellite *which* is running — "action", "seed", or "" for
    none.  The flag the HUD lights its loop button from, and the place the loop key
    steps on from."""
    return state.portrait_loop if which == 2 else state.landscape_loop


def _side_f_mode(state: BridgeState, which: int) -> bool:
    """Whether satellite *which* is in F-mode — the flag every rebuild of that side
    has to carry, or the rebuild would quietly widen it back to the whole library."""
    return state.portrait_f_mode if which == 2 else state.landscape_f_mode


def _set_side_f_mode(state: BridgeState, which: int, enabled: bool) -> BridgeState:
    """Record whether satellite *which* is in F-mode.  Set before the side's next
    rebuild, since every rebuild reads the flag back off the state to know how wide
    to build."""
    if which == 2:
        return replace(state, portrait_f_mode=enabled)
    return replace(state, landscape_f_mode=enabled)


def _set_side_filter(state: BridgeState, which: int, query: str) -> BridgeState:
    if which == 2:
        return replace(state, portrait_filter=query)
    return replace(state, landscape_filter=query)


def _rebuild_side(
    which: int, query: str, state: BridgeState, config: BridgeConfig,
    *, start_at_top: bool = False,
) -> SatelliteFilterFlowResult:
    """Rebuild one satellite's browse under *query* and its own current ordering.

    The single place a satellite's playlist is rebuilt from its sources, so a
    filter and a reorder cannot drift apart in how they read the side's state.
    ``start_at_top`` is for the callers that mean "start over" — a reorder or a
    reset — since the reload otherwise keeps the clip on screen and carries on from
    where it sat, leaving the new order to apply only behind it.
    """
    return apply_satellite_filter(
        which=which,
        query=query,
        f_mode_enabled=_side_f_mode(state, which),
        recent=state.portrait_latest if which == 2 else state.landscape_latest,
        sources=config.portrait_sources if which == 2 else config.landscape_sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        cmd_file=config.satellite_cmd_file(which),
        start_at_top=start_at_top,
        regen_media_root=config.regen_media_root,
        regen_metadata_root=config.regen_metadata_root,
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
            state = _set_side_filter(state, which, query)
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
        current_mode=state.main_mode,
        target_mode=target_mode,
        omni_paused=state.omni_paused,
        genau_paused_file=config.genau_paused_file,
        audio_paused_file=config.audio_paused_file,
        genau_cmd_file=config.genau_cmd_file,
        nau_paused_file=config.nau_paused_file,
        nau_cmd_file=config.nau_cmd_file,
        broker_cmd_file=config.broker_cmd_file,
    )
    state = replace(state, main_mode=result.next_mode)
    if result.is_transition:
        ops.extend(_main_slot_ops(result.next_mode))
    if result.log_message:
        logger.info(result.log_message)
    return state, ops


@functools.lru_cache(maxsize=1)
def _clipper_project_dir() -> Path:
    """The sibling clipper checkout, found from the main player rather than from here.

    ``__file__`` names whichever checkout is running, and a branch-verification
    session runs from a worktree — where ``../clipper`` is a directory under
    ``.claude/worktrees`` that does not exist, so the save died in the ``cwd=``
    and the hotkey looked like the branch had broken it.  The siblings live
    beside the *primary* checkout, which every worktree can name (they share its
    git directory).  Cached: it is one subprocess, and the answer cannot change
    while the session runs.  Anywhere git cannot answer, this checkout is the
    best guess there is, which is also the old behavior.
    """
    from .branch_session import primary_checkout  # noqa: PLC0415 — avoids a launcher import on the hot path

    try:
        return primary_checkout().parent / "clipper"
    except (OSError, subprocess.SubprocessError):
        return Path(__file__).resolve().parents[1].parent / "clipper"


def _clipper_python() -> str:
    clipper_python = _clipper_project_dir() / ".venv" / "Scripts" / "python.exe"
    if clipper_python.is_file():
        return str(clipper_python)
    return sys.executable


def _current_main_media(config: BridgeConfig) -> tuple[str, float]:
    """The main player's current video path and playback time (seconds).

    Nau owns the main player in every mode it appears (nau and hybrid) and
    publishes both in its status file; the path is empty when nothing is playing.
    """
    status = read_nau_status(config.nau_status_file)
    return status.video, status.position_ms / 1000


def _dispatch_clipper_save(config: BridgeConfig) -> str:
    """Save a Clipper session for the main player's current video.

    Returns a short user-visible message on success, or empty string on failure.
    """
    video_path, playback_time = _current_main_media(config)
    if not video_path:
        logger.warning("clipper_save: no video playing on the main player")
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
            cwd=str(_clipper_project_dir()),
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
