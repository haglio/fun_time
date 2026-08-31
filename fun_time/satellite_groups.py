"""The satellites' clip-grouping engine: sibling cycles, group loops, the loop
key's cycle, seed widening and HUD map navigation.  Slot-addressed (2=portrait,
3=landscape); the dispatcher owns which command id reaches which entry point."""
from __future__ import annotations

import logging
from pathlib import Path

from .bridge_records import (
    FAILED_NOTICE_LEVEL,
    FAVORITE_NOTICE_LEVEL,
    BridgeConfig,
    WindowOp,
)
from .event_log import SOURCE_LANDSCAPE, SOURCE_PORTRAIT
from .lock_hud import cell_path, hud_map_cells, locate_cell, navigate_cell
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
from .modes import collect_video_files, write_playlist_file
from .runtime_flow import satellite_browse_paths
from .satellite_control import read_satellite_status, write_satellite_command
from .shared_state import BridgeState

logger = logging.getLogger(__name__)


def satellite_source(which: int) -> str:
    """The event-log source for satellite slot *which* (2=portrait, 3=landscape)."""
    return SOURCE_PORTRAIT if which == 2 else SOURCE_LANDSCAPE


def same_video(left: str, right: str) -> bool:
    """Whether two paths name the same clip, under the library's path normalization."""
    return normalize_path_key(left) == normalize_path_key(right)


def satellite_current(config: BridgeConfig, which: int) -> str:
    """The video a satellite is showing now, read from its published status file."""
    return read_satellite_status(config.side(which).status_file).video


def send_satellite(config: BridgeConfig, which: int, verb: str) -> None:
    """Queue one verb on a satellite's command file for the player to drain."""
    write_satellite_command(config.side(which).cmd_file, verb)


def play_video(config: BridgeConfig, which: int, path: str) -> None:
    """Make *path* the satellite's current clip.

    ``PLAY_FILE`` is the native player's jump-or-splice: it jumps to the clip if
    it is already queued, else splices it in after the current clip and plays it.
    """
    send_satellite(config, which, f"PLAY_FILE {path}")


def cancel_lock(which: int, state: BridgeState, config: BridgeConfig) -> BridgeState:
    """Release a repeat-one lock so the side auto-advances again.

    A locked satellite is holding one clip (``LOCK`` → mpv ``loop_file``); the
    ``UNLOCK`` verb restores end-of-file playlist advance.  A no-op when the side
    was not locked.
    """
    if state.side(which).locked:
        send_satellite(config, which, "UNLOCK")
    return state.with_side(which, locked=False)


def clear_side_grouping(state: BridgeState, which: int) -> BridgeState:
    """Forget any group loop AND any widened seed row on *which* satellite — its
    playlist was rebuilt or re-navigated, which drops both.  A no-op when neither
    was set.  The widen only ever means something in the context of the clip/loop
    it was taken around, so a rebuild that drops the loop drops the widen with it."""
    return state.with_side(which, loop="", map_anchor="", widen_clip="")


def _satellite_group_index(which: int, config: BridgeConfig, current: str) -> GroupIndex:
    """The cached grouping index over a satellite's sources, fresh for *current*."""
    sources = config.side(which).sources
    return cached_group_index(
        sources,
        paths_supplier=lambda: collect_video_files(sources),
        metadata_root=config.regen_metadata_root,
        must_contain=current,
    )


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


def video_action_label(video_path: str, config: BridgeConfig) -> str:
    """The act recorded in *video_path*'s sidecar, or "" when it has none."""
    meta_path = metadata_path_for(video_path, config.regen_metadata_root)
    if meta_path is None or not meta_path.is_file():
        return ""
    video = load_metadata(meta_path).get("video") or {}
    return str(video.get("action") or "").strip()


def cycle_variant(
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
    source = satellite_source(which)
    ops: list[WindowOp] = []
    current = target_path or satellite_current(config, which)
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
    play_video(config, which, target)
    if kind == "action":
        # Numbered when the group holds several of the same act ("Alpha 2").
        action = action_label(index, target)
        if action:
            ops.append(WindowOp(op="notice", key=f"Action: {action}", source=source))
    else:
        ops.append(WindowOp(op="notice", key="Next seed", source=source))
    return state, ops


def more_seeds(
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
    source = satellite_source(which)
    current = target_path or satellite_current(config, which)
    if not current:
        return state, []
    index = _satellite_group_index(which, config, current)
    current_key = normalize_path_key(current)
    exact = {normalize_path_key(m) for m in seed_family_members(index, current)} - {current_key}
    wide = {normalize_path_key(m) for m in widened_seed_members(index, current)} - {current_key}
    if wide <= exact:
        return state, [WindowOp(op="notice", key="Widening net failed", source=source, level=FAILED_NOTICE_LEVEL)]
    state = state.with_side(which, widen_clip=current)
    # Loop the pool that was just widened: the widen anchor now matches the clip on
    # screen, so the loop gathers the wider row the HUD draws.  This starts a loop
    # where none was running and re-shapes one that was.  Its notices are dropped —
    # "More seeds" is the one thing that happened, from the user's side.
    state, _loop_ops = group_loop(which, "seed", state, config, target_path=current)
    return state, [WindowOp(op="notice", key="More seeds", source=source)]


def wrong_action(
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
    source = satellite_source(which)
    current = target_path or satellite_current(config, which)
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
    widened = axis == "seed" and same_video(state.side(which).widen_clip, current)
    gather = widened_seed_members if widened else (
        action_group_members if axis == "action" else seed_family_members
    )
    return [member for member in gather(index, current) if Path(member).exists()], widened


def group_loop(
    which: int, axis: str, state: BridgeState, config: BridgeConfig, target_path: str = ""
) -> tuple[BridgeState, list[WindowOp]]:
    """Loop the satellite around the current clip's action group or seed family."""
    source = satellite_source(which)
    ops: list[WindowOp] = []
    current = target_path or satellite_current(config, which)
    if not current:
        return state, ops
    members, widened = _loop_members(which, axis, state, config, current)
    if len(members) < 2:
        # Only this clip is in the group, so "looping" it is a single-video lock:
        # LOCK this one.  Never a dead end — the loop buttons are still valid with
        # one video, they just mean "lock" then.  A lock is not a loop, so any
        # prior loop (and widened row) is dropped.
        send_satellite(config, which, "LOCK")
        state = state.with_side(which, locked=True)
        state = clear_side_grouping(state, which)
        # Green: locking a clip puts it in the favorites, so it says so in the
        # color the favorites own.
        return state, [WindowOp(op="notice", key="Locked", source=source,
                                level=FAVORITE_NOTICE_LEVEL)]
    # A loop is repeat-all over the group, so a repeat-one lock must go first.
    state = cancel_lock(which, state, config)
    # Write the group as the side's playlist with the current clip first, then
    # RELOAD_PLAYLIST: the native player keeps the current clip playing when it
    # survives the reload, so the clip on screen is never restarted and only what
    # comes up next becomes the group, which then cycles by auto-advance.
    members = [current] + [m for m in members if normalize_path_key(m) != normalize_path_key(current)]
    write_playlist_file(config.side(which).playlist_file, members)
    send_satellite(config, which, "RELOAD_PLAYLIST")
    label = "portrait" if which == 2 else "landscape"
    message = f"Loop {label}: {len(members)} {axis}s"
    logger.info(message)
    state = state.with_side(which, loop=axis, map_anchor=current)
    # Anchor the widen on the loop iff it is the loose family being looped, so the
    # HUD reads a running seed loop as widened exactly when it truly is — and a
    # plain exact-family loop drops any stale anchor.
    state = state.with_side(which, widen_clip=current if widened else "")
    ops.append(WindowOp(op="notice", key=message, source=source))
    return state, ops


# What a satellite's one loop key steps through, in order: the seed family, then
# the action group, then off — and round to the seed family again.  "" is the off
# stop, and is where a side that is not looping already stands, so the first press
# starts a seed loop.
_LOOP_CYCLE: tuple[str, ...] = ("seed", "action", "")


def loop_cycle(
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
    current = target_path or satellite_current(config, which)
    if not current:
        return state, []
    # Which loop the side is running — the flag the HUD lights its loop button
    # from, so the key and the HUD can never disagree.
    running = state.side(which).loop
    # An unknown flag (a hand-edited state file) reads as "not looping", so the
    # cycle starts over at its first axis rather than raising.
    start = _LOOP_CYCLE.index(running) + 1 if running in _LOOP_CYCLE else 0
    for step in range(len(_LOOP_CYCLE)):
        axis = _LOOP_CYCLE[(start + step) % len(_LOOP_CYCLE)]
        if not axis:
            if running:
                return no_loop("portrait" if which == 2 else "landscape", state, config)
            continue  # nothing is looping, so the off step has nothing to switch off
        if len(_loop_members(which, axis, state, config, current)[0]) >= 2:
            return group_loop(which, axis, state, config, current)
    if state.side(which).locked:
        state = cancel_lock(which, state, config)
        return state, [WindowOp(op="notice", key="Unlocked", source=satellite_source(which))]
    # The lone-clip loop's own lock, so the press means exactly what "loop seeds"
    # would have meant on this clip.
    return group_loop(which, _LOOP_CYCLE[0], state, config, current)


def _browse_behind(browse: list[str], current: str) -> list[str]:
    """*browse*, guaranteed to still hold *current* — the clip on screen.

    The player keeps its clip across a playlist reload only while the new list
    still holds it, and a loop member usually is not in the browse: the browse
    picks one clip per group and the loop was cycling that group's others.  So the
    clip on screen heads the restored list — it plays to its own end and the browse
    is simply what comes up next.  A browse that already holds it keeps its own
    order, which the reload resumes from wherever the clip sits.
    """
    if not current or any(same_video(path, current) for path in browse):
        return browse
    return [current, *browse]


def no_loop(
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
    current = satellite_current(config, which)
    side = state.side(which)
    browse = satellite_browse_paths(
        query=side.filter,
        f_mode_enabled=side.f_mode,
        recent=side.latest,
        sources=config.side(which).sources,
        favs_file=config.favs_file,
        state_dir=config.state_dir,
        regen_metadata_root=config.regen_metadata_root,
    )
    # A non-empty filter that now matches nothing would blank the queue, so the
    # browse is only reshaped when it actually has clips; otherwise the loop's
    # queue keeps playing and just the flag clears.
    if browse:
        write_playlist_file(config.side(which).playlist_file, _browse_behind(browse, current))
        send_satellite(config, which, "RELOAD_PLAYLIST")
    # Only the loop itself goes.  The map anchor and any widened row stay, so the HUD
    # keeps hanging exactly where it was and switching a loop off takes away the lit
    # button and the rectangle and nothing else; the map lets go by itself once the
    # browse moves on past the group.
    state = state.with_side(which, loop="")
    return state, [WindowOp(op="notice", key="Loop off", source=satellite_source(which))]


def switch_to_video(
    which: int, path: str, state: BridgeState, config: BridgeConfig
) -> tuple[BridgeState, list[WindowOp]]:
    """Switch a satellite straight to *path* — the command a HUD thumbnail click
    sends.  Plays it from the playlist if it is already there, else splices it in
    after the current clip, exactly as cycling to a sibling does."""
    if not path:
        return state, []
    play_video(config, which, path)
    return state, [WindowOp(op="notice", key="Switched", source=satellite_source(which))]


def navigate_hud(
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
    source = satellite_source(which)
    current = satellite_current(config, which)
    if not current:
        return state, []
    index = _satellite_group_index(which, config, current)
    anchor = state.side(which).nav_anchor
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
    if target_cell == cell or not target or same_video(target, current):
        state = state.with_side(which, nav_anchor=anchor)
        return state, [WindowOp(op="notice", key="No clip that way", source=source, level=FAILED_NOTICE_LEVEL)]
    state = state.with_side(which, nav_anchor=root)
    return switch_to_video(which, target, state, config)
