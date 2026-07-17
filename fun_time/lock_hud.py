"""Framework-free logic for the per-VLC lock-status HUD.

Assembles what each satellite's HUD overlay shows — its lock state and the
other videos reachable in the current clip's action group and seed family — and
the configuration and geometry the overlay process needs. Kept free of Qt so it
is unit-testable; the Qt overlay in :mod:`fun_time.lock_hud_app` renders it.
"""
from __future__ import annotations

import configparser
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fun_time.config import LayoutConfig
from fun_time.media_metadata import (
    GroupIndex,
    action_group_members,
    cached_group_index,
    loose_seed_family_members,
    normalize_path_key,
    seed_family_members,
)
from fun_time.modes import collect_video_files
from fun_time.thumbnail_cache import thumbnail_for
from fun_time.window_layout import WindowRect

THUMBNAIL_CACHE_DIRNAME = "hud_thumbnails"

# The dispatch loop rewrites this beside the manifest after every command; it
# carries the locks, the per-satellite filters and the primary display's sound.
SHARED_STATE_FILENAME = "shared_bridge_state.ini"

# The HUD process touches this beside the manifest once its indexes are primed;
# startup waits for it before dropping the loading screen (see
# windows_bridge_sequencer), so the maps are ready the instant Fun Time appears.
HUD_READY_FILENAME = "lock_hud_ready.txt"

# The orchestrator records this session's child PIDs here at startup (beside the
# manifest); the HUD reads it to tell whether the foreground window is Fun Time's.
BRIDGE_PIDS_FILENAME = "bridge_pids.ini"

# Inset (px) of the HUD from its satellite's exact top-left corner.
HUD_MARGIN = 12


def overlay_rect(vlc_rect: WindowRect, *, width: int, height: int, margin: int = HUD_MARGIN) -> WindowRect:
    """The HUD overlay's screen rect, pinned to *vlc_rect*'s top-left corner."""
    return WindowRect(
        x=vlc_rect.x + margin,
        y=vlc_rect.y + margin,
        width=width,
        height=height,
    )


def hud_overlays_visible(loading_active: bool) -> bool:
    """Whether the overlays should be shown right now.

    Hidden only while the loading overlay is up, so they never flash
    mid-startup.  Shown whenever it is down — OmniPause included: OmniPause
    pauses playback, but the map stays up so you can still see (and click) what
    each satellite is holding.  Whether a *shown* overlay also holds the topmost
    band is a separate question — see :func:`hud_should_be_topmost`.
    """
    return not loading_active


def hud_should_be_topmost(foreground_pid: int, fun_time_pids: set[int]) -> bool:
    """Whether the HUD should hold the topmost band on this refresh.

    True only while the foreground window belongs to Fun Time (its owning process
    is one of *fun_time_pids*): then the overlay floats above its satellite as
    intended.  When the user has switched to another app the foreground PID is a
    stranger, so this is False and the overlay drops out of the band instead of
    sitting over that app — the OmniPause "HUD covers everything" regression.
    A null/absent foreground (pid 0, never in the set) also reads as "not ours".
    """
    return foreground_pid in fun_time_pids


def load_fun_time_pids(pids_file: Path, own_pid: int) -> set[int]:
    """The PIDs of this Fun Time session's windows — the two satellite VLCs, the
    primary-slot players, the dashboard — as the orchestrator recorded them in
    ``bridge_pids.ini``, plus this HUD's own *own_pid*.

    Used to decide whether the foreground window is Fun Time's (see
    :func:`hud_should_be_topmost`).  A missing or half-written file yields just
    ``{own_pid}``: the safe default is to treat nothing else as ours and let the
    overlay drop, never to stay stuck on top.  A never-launched child (pid 0)
    owns no window, so it is dropped.
    """
    pids = {own_pid}
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(str(pids_file), encoding="utf-8")
    for value in parser["pids"].values() if parser.has_section("pids") else ():
        try:
            pid = int(value)
        except ValueError:
            continue
        if pid > 0:
            pids.add(pid)
    return pids


@dataclass(frozen=True)
class HudAppConfig:
    """Everything the HUD overlay process needs, read from the bridge manifest.

    Sourced from the same ``windows_bridge_launch.ini`` the dispatch loop reads,
    so the HUD indexes the satellite libraries with the identical sources and
    roots production uses — never a hand-crafted duplicate.
    """

    layout: LayoutConfig
    portrait_port: int
    landscape_port: int
    vlc_password: str
    portrait_sources: str
    landscape_sources: str
    provider_media_root: Path | None
    provider_metadata_root: Path | None
    shared_state_file: Path
    thumbnail_cache_dir: Path
    # Where the HUD posts commands (thumbnail clicks) for the dispatch loop to
    # pick up — the same file the dashboard writes, read the same way.
    dashboard_cmd_file: Path
    # The HUD touches this once its indexes are primed; startup waits for it
    # before tearing down the loading screen, so Fun Time isn't revealed with
    # the maps not yet ready.
    ready_file: Path


def load_hud_app_config(manifest_path: str | Path) -> HudAppConfig:
    """Parse the windows-bridge manifest into the HUD's configuration."""
    manifest_path = Path(manifest_path)
    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(manifest_path, encoding="utf-8")

    def _root(key: str) -> Path | None:
        raw = parser.get("provider_regen", key, fallback="").strip()
        return Path(raw) if raw else None

    layout = LayoutConfig(
        main_monitor=parser.getint("layout", "main_monitor"),
        secondary_monitor=parser.getint("layout", "secondary_monitor"),
        primary_top_ratio=parser.getfloat("layout", "primary_top_ratio"),
        landscape_width_ratio=parser.getfloat("layout", "landscape_width_ratio"),
    )
    return HudAppConfig(
        layout=layout,
        portrait_port=parser.getint("vlc", "vlc2_port", fallback=8091),
        landscape_port=parser.getint("vlc", "vlc3_port", fallback=8092),
        vlc_password=parser.get("vlc", "vlc_pass", fallback=""),
        portrait_sources=parser.get("media", "portrait_dirs", fallback=""),
        landscape_sources=parser.get("media", "landscape_dirs", fallback=""),
        provider_media_root=_root("media_root"),
        provider_metadata_root=_root("metadata_root"),
        shared_state_file=manifest_path.parent / SHARED_STATE_FILENAME,
        thumbnail_cache_dir=manifest_path.parent / THUMBNAIL_CACHE_DIRNAME,
        dashboard_cmd_file=Path(
            parser.get("commands", "dashboard_cmd_file", fallback=str(manifest_path.parent / "dashboard_cmd.txt"))
        ),
        ready_file=manifest_path.parent / HUD_READY_FILENAME,
    )


def _lock_label(locked: bool, lock_type: str | None) -> str:
    """The status word for the lock band.

    Today a lock is just repeat-one on the current clip, so this is
    "Locked"/"Unlocked". When distinct lock *types* land, *lock_type* flows in
    here — "Locked · seed" — without the callers changing.
    """
    if not locked:
        return "Unlocked"
    return f"Locked · {lock_type}" if lock_type else "Locked"


@dataclass(frozen=True)
class HudPanel:
    """One satellite's HUD contents (portrait or landscape).

    The current clip anchors the map: seeds (the same act, other subjects) run
    right from it, and distinct other actions run down from it.
    """

    side: str
    locked: bool
    lock_label: str
    current: str
    seed_siblings: list[str]
    action_siblings: list[str]
    # Labels for the map's axes: the current clip's own action (the top row),
    # and each action sibling's action name (the rows down the column). Seed
    # columns are labelled by ordinal ("Seed 1", …) so need no data here.
    current_action: str = ""
    action_labels: tuple[str, ...] = ()
    filter_query: str = ""
    # Which axis this side is looping ("" none / "action" / "seed").  While a
    # loop runs, ``current`` is frozen to the group's anchor (so the map does not
    # re-orient as the clip auto-advances) and ``playing`` names the map cell
    # actually on screen, which the overlay lights up.  With no loop, ``playing``
    # is just ``current`` — the corner.
    active_loop: str = ""
    playing: str = ""


def _others(members: list[str], current: str) -> list[str]:
    """*members* without *current* itself — the clips you could reach from here."""
    key = normalize_path_key(current)
    return [member for member in members if normalize_path_key(member) != key]


def _distinct_action_siblings(index: GroupIndex, current: str) -> list[str]:
    """One representative clip per OTHER action in the current clip's group.

    The action axis steps between distinct acts, so same-act twins collapse to a
    single entry and the current clip's own act is left out — it is the corner.
    """
    current_key = normalize_path_key(current)
    current_action = index.action_by_path.get(current_key, "")
    reps: list[str] = []
    seen: set[str] = set()
    for member in action_group_members(index, current):
        action = index.action_by_path.get(normalize_path_key(member), "")
        if not action or action == current_action or action in seen:
            continue
        seen.add(action)
        reps.append(member)
    return reps


# Map navigation geometry
# -----------------------
# The map is an L: the current clip anchors the corner, its seed family runs
# right (columns) and its distinct other actions run down (rows).  These
# framework-free helpers let a keyboard selection move around that L and resolve
# a cell to the clip drawn there, so the dispatch loop can drive the map from the
# arrow / WASD keys exactly as a thumbnail click drives it.

# How many seed columns and action rows the overlay draws — navigation walks the
# same capped lists so a selection never lands on a thumbnail that was dropped
# for want of room.
SEED_LIMIT = 6
ACTION_LIMIT = 4

Cell = tuple[str, int]  # ("corner", 0) | ("seed", i) | ("action", i)


def navigate_cell(cell: Cell, direction: str, *, seed_count: int, action_count: int) -> Cell:
    """The cell reached by moving *direction* from *cell* on the L-shaped map.

    Right/left walk the seed row, down/up the action column; the corner joins
    both axes.  Movement clamps at each end and no-ops off the axis it is on (a
    seed has nothing below it, an action nothing to its right), so an at-the-edge
    press simply returns *cell* unchanged.
    """
    bucket, index = cell
    if direction == "right":
        if bucket == "corner":
            return ("seed", 0) if seed_count else cell
        if bucket == "seed":
            return ("seed", index + 1) if index + 1 < seed_count else cell
        return cell
    if direction == "left":
        if bucket == "seed":
            return ("seed", index - 1) if index >= 1 else ("corner", 0)
        return cell
    if direction == "down":
        if bucket == "corner":
            return ("action", 0) if action_count else cell
        if bucket == "action":
            return ("action", index + 1) if index + 1 < action_count else cell
        return cell
    if direction == "up":
        if bucket == "action":
            return ("action", index - 1) if index >= 1 else ("corner", 0)
        return cell
    return cell


def locate_cell(current: str, corner: str, seeds: list[str], actions: list[str]) -> Cell | None:
    """Which cell holds *current* — the corner, a seed or an action — by exact
    path match, or None when *current* is not drawn on the map at all (e.g. the
    satellite auto-advanced off the family)."""
    key = normalize_path_key
    if key(current) == key(corner):
        return ("corner", 0)
    for i, path in enumerate(seeds):
        if key(path) == key(current):
            return ("seed", i)
    for i, path in enumerate(actions):
        if key(path) == key(current):
            return ("action", i)
    return None


def cell_path(cell: Cell, corner: str, seeds: list[str], actions: list[str]) -> str:
    """The clip drawn at *cell*, or "" when the cell index is out of range."""
    bucket, index = cell
    if bucket == "corner":
        return corner
    members = seeds if bucket == "seed" else actions if bucket == "action" else []
    return members[index] if 0 <= index < len(members) else ""


def hud_map_cells(
    index: GroupIndex, anchor: str, *, seed_limit: int = SEED_LIMIT, action_limit: int = ACTION_LIMIT
) -> tuple[list[str], list[str]]:
    """The seed-row and action-column clips the map draws around *anchor*, capped
    at the overlay's draw limits.  The seed row is the exact family — keyboard
    navigation walks the core family, never the widened "more seeds" pool — so the
    axes match what a fresh (un-widened) map shows."""
    seeds = _others(seed_family_members(index, anchor), anchor)[:seed_limit]
    actions = _distinct_action_siblings(index, anchor)[:action_limit]
    return seeds, actions


def _playing_member(
    index: GroupIndex, anchor: str, current: str, seed: list[str], action: list[str], axis: str
) -> str:
    """Which drawn map cell the live *current* clip is — the one the overlay
    lights up.  On the seed axis the clip is itself a seed cell; on the action
    axis it is represented by the sibling sharing its action."""
    key = normalize_path_key
    if key(current) == key(anchor):
        return anchor
    if axis == "seed":
        return current
    current_action = index.action_by_path.get(key(current), "")
    if current_action == index.action_by_path.get(key(anchor), ""):
        return anchor
    for member in action:
        if index.action_by_path.get(key(member), "") == current_action:
            return member
    return anchor


def build_hud_panel(
    side: str,
    *,
    locked: bool,
    current: str,
    index: GroupIndex | None,
    lock_type: str | None = None,
    filter_query: str = "",
    loop_axis: str = "",
    widen: bool = False,
    nav_anchor: str = "",
) -> HudPanel:
    """The HUD panel for *side*, given its lock flag, current clip and index.

    Seeds come from the same helper the loop commands use, so the row is exactly
    what looping the seed axis would cycle through; the action column collapses
    to one clip per distinct other act.  When *widen* is set ("more seeds"), the
    seed row grows to the loose family — the same scene re-rendered with a knob or
    seed freed — instead of just the exact parameter set.

    When *loop_axis* names a running loop, the map anchors on the looped group's
    fixed representative instead of the live clip, so it does not re-orient as the
    loop auto-advances; ``playing`` then marks the cell that is actually on screen.

    ``nav_anchor`` does the same for keyboard navigation: while it names a clip and
    the live clip is still one of that clip's map cells, the map freezes on it and
    ``playing`` lights the cell on screen, so an arrow-driven selection moves across
    a stable map.  Once the satellite drifts off the frozen map (an auto-advance),
    the anchor is abandoned and the map re-homes on the live clip.  A running loop
    wins over a nav anchor.
    """
    have_siblings = bool(current) and index is not None
    anchor = current
    active_loop = ""
    nav_frozen = False
    if have_siblings and loop_axis in ("seed", "action"):
        gather = seed_family_members if loop_axis == "seed" else action_group_members
        group = gather(index, current)
        if len(group) >= 2:
            # Anchor on the group's lowest-keyed member — the same clip whichever
            # member is playing — so the map holds still while the loop advances.
            anchor = min(group, key=normalize_path_key)
            active_loop = loop_axis
    elif have_siblings and nav_anchor and normalize_path_key(nav_anchor) != normalize_path_key(current):
        nav_seed, nav_action = hud_map_cells(index, nav_anchor)
        if locate_cell(current, nav_anchor, nav_seed, nav_action) is not None:
            anchor = nav_anchor
            nav_frozen = True
    # Navigation walks the exact family (never widened), so a frozen map matches
    # what the keys can reach.
    seed_pool = loose_seed_family_members if widen and not nav_frozen else seed_family_members
    seed = _others(seed_pool(index, anchor), anchor) if have_siblings else []
    action = _distinct_action_siblings(index, anchor) if have_siblings else []
    current_action = ""
    action_labels: tuple[str, ...] = ()
    playing = anchor
    if have_siblings:
        current_action = index.action_by_path.get(normalize_path_key(anchor), "")
        action_labels = tuple(
            index.action_by_path.get(normalize_path_key(member), "") for member in action
        )
        if active_loop:
            playing = _playing_member(index, anchor, current, seed, action, active_loop)
        elif nav_frozen:
            playing = current  # the live clip is exactly the cell to light
    return HudPanel(
        side=side,
        locked=locked,
        lock_label=_lock_label(locked, lock_type),
        current=anchor,
        seed_siblings=seed,
        action_siblings=action,
        current_action=current_action,
        action_labels=action_labels,
        filter_query=filter_query,
        active_loop=active_loop,
        playing=playing,
    )


def _side_panel(
    config: HudAppConfig, side: str, sources: str, current: str, locked: bool,
    filter_query: str, loop_axis: str, widen: bool, nav_anchor: str,
) -> HudPanel:
    index: GroupIndex | None = None
    if current:
        # must_contain=None: read the up-front index (see prime_group_indexes),
        # never a per-clip rebuild — the library does not change during a session,
        # so the map is drawn from memory the instant the clip changes.
        index = cached_group_index(
            sources,
            paths_supplier=lambda: collect_video_files(sources),
            metadata_root=config.provider_metadata_root,
            must_contain=None,
        )
    return build_hud_panel(
        side, locked=locked, current=current, index=index,
        filter_query=filter_query, loop_axis=loop_axis, widen=widen, nav_anchor=nav_anchor,
    )


def prime_group_indexes(config: HudAppConfig) -> None:
    """Build both satellites' group indexes up front — behind the loading screen,
    before the first clip is drawn — so the map is instant on the first refresh
    and no later refresh pays for a rebuild.  The library is fixed for the run,
    so one build is enough (premiere is what would extend it)."""
    for sources in (config.portrait_sources, config.landscape_sources):
        if sources:
            cached_group_index(
                sources,
                paths_supplier=lambda captured=sources: collect_video_files(captured),
                metadata_root=config.provider_metadata_root,
                must_contain=None,
            )


def prewarm_thumbnails(
    config: HudAppConfig,
    thumbnailer: Callable[[str, str | Path], object] = thumbnail_for,
    sleep_fn: Callable[[float], None] = time.sleep,
    pause_s: float = 0.05,
) -> None:
    """Extract and cache every library clip's thumbnail in the background, so the
    map paints from cache instead of blocking on a first-use frame grab.
    Idempotent — an already cached thumbnail is skipped.  Sleeps briefly between
    clips so decoding a big HEVC library never starves the HUD's own paint (that
    starvation was showing up as multi-second blinks); run it off the UI thread."""
    for sources in (config.portrait_sources, config.landscape_sources):
        if not sources:
            continue
        for path in collect_video_files(sources):
            thumbnailer(path, config.thumbnail_cache_dir)
            sleep_fn(pause_s)


def signal_hud_ready(ready_file: str | Path) -> None:
    """Mark the HUD ready to be shown — its indexes are primed, so its first
    paint is instant.  Startup waits on this before dropping the loading screen
    (see :func:`wait_for_hud_ready`), so Fun Time never appears with blank maps."""
    Path(ready_file).write_text("ready", encoding="utf-8")


def wait_for_hud_ready(
    ready_file: str | Path,
    *,
    timeout_s: float,
    poll_s: float = 0.1,
    sleep_fn: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Block until the HUD has signalled ready, or *timeout_s* elapses.

    Returns whether the flag appeared.  The timeout is a hard cap: a HUD that
    never primes (or was never launched) must not wedge startup — the caller
    reveals Fun Time anyway once it lapses.
    """
    ready = Path(ready_file)
    deadline = clock() + timeout_s
    while clock() < deadline:
        if ready.exists():
            return True
        sleep_fn(poll_s)
    return ready.exists()


def build_panels(
    config: HudAppConfig,
    *,
    portrait_current: str,
    landscape_current: str,
    portrait_locked: bool,
    landscape_locked: bool,
    portrait_filter: str = "",
    landscape_filter: str = "",
    portrait_loop: str = "",
    landscape_loop: str = "",
    portrait_widen_clip: str = "",
    landscape_widen_clip: str = "",
    portrait_nav_anchor: str = "",
    landscape_nav_anchor: str = "",
) -> tuple[HudPanel, HudPanel]:
    """Both satellites' HUD panels, indexing each side from its own sources.

    The group index is built (and cached) per side exactly as ``_cycle_variant``
    does, so the siblings shown match what cycling would actually reach.  A side's
    seed row is widened only while its widen-clip still matches the clip on
    screen, so the widen auto-resets on navigation.  A side's ``nav_anchor``
    freezes its map on the clip keyboard navigation began from (see
    ``build_hud_panel``).
    """
    def widened(clip: str, current: str) -> bool:
        return bool(clip) and normalize_path_key(clip) == normalize_path_key(current)

    return (
        _side_panel(
            config, "portrait", config.portrait_sources,
            portrait_current, portrait_locked, portrait_filter, portrait_loop,
            widened(portrait_widen_clip, portrait_current), portrait_nav_anchor,
        ),
        _side_panel(
            config, "landscape", config.landscape_sources,
            landscape_current, landscape_locked, landscape_filter, landscape_loop,
            widened(landscape_widen_clip, landscape_current), landscape_nav_anchor,
        ),
    )


def panel_thumbnails(
    paths: list[str],
    cache_dir: str | Path,
    *,
    limit: int,
    thumbnailer: Callable[[str, str | Path], Path | None] = thumbnail_for,
) -> list[tuple[str, Path]]:
    """Up to *limit* ``(video_path, thumbnail_path)`` pairs, skipping unreadable clips.

    Iterates *paths* in order until *limit* thumbnails resolve, so a clip whose
    frame cannot be read simply drops out rather than leaving a gap.
    """
    resolved: list[tuple[str, Path]] = []
    for path in paths:
        if len(resolved) >= limit:
            break
        thumb = thumbnailer(path, cache_dir)
        if thumb is not None:
            resolved.append((path, thumb))
    return resolved
