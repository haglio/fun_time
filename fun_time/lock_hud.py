"""Framework-free logic for the per-VLC lock-status HUD.

Assembles what each satellite's HUD overlay shows — its lock state and the
other videos reachable in the current clip's action group and seed family — and
the configuration and geometry the overlay process needs. Kept free of Qt so it
is unit-testable; the Qt overlay in :mod:`fun_time.lock_hud_app` renders it.
"""
from __future__ import annotations

import configparser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fun_time.config import LayoutConfig
from fun_time.media_metadata import (
    GroupIndex,
    action_group_members,
    cached_group_index,
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


def hud_display_state(loading_active: bool, omni_paused: bool) -> tuple[bool, bool]:
    """``(visible, desired_topmost)`` for the overlays right now.

    Hidden while the loading overlay is up, so they never flash mid-startup.
    Visible under OmniPause but *not* topmost: OmniPause must free the desktop,
    so the overlays leave the topmost band (the app clears it) instead of
    staying glued above everything.
    """
    visible = not loading_active
    return visible, visible and not omni_paused


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


def build_hud_panel(
    side: str,
    *,
    locked: bool,
    current: str,
    index: GroupIndex | None,
    lock_type: str | None = None,
    filter_query: str = "",
) -> HudPanel:
    """The HUD panel for *side*, given its lock flag, current clip and index.

    Seeds come from the same helper the loop commands use, so the row is exactly
    what looping the seed axis would cycle through; the action column collapses
    to one clip per distinct other act.
    """
    have_siblings = bool(current) and index is not None
    seed = _others(seed_family_members(index, current), current) if have_siblings else []
    action = _distinct_action_siblings(index, current) if have_siblings else []
    current_action = ""
    action_labels: tuple[str, ...] = ()
    if have_siblings:
        current_action = index.action_by_path.get(normalize_path_key(current), "")
        action_labels = tuple(
            index.action_by_path.get(normalize_path_key(member), "") for member in action
        )
    return HudPanel(
        side=side,
        locked=locked,
        lock_label=_lock_label(locked, lock_type),
        current=current,
        seed_siblings=seed,
        action_siblings=action,
        current_action=current_action,
        action_labels=action_labels,
        filter_query=filter_query,
    )


def _side_panel(
    config: HudAppConfig, side: str, sources: str, current: str, locked: bool, filter_query: str
) -> HudPanel:
    index: GroupIndex | None = None
    if current:
        index = cached_group_index(
            sources,
            paths_supplier=lambda: collect_video_files(sources),
            metadata_root=config.provider_metadata_root,
            must_contain=current,
        )
    return build_hud_panel(
        side, locked=locked, current=current, index=index, filter_query=filter_query
    )


def build_panels(
    config: HudAppConfig,
    *,
    portrait_current: str,
    landscape_current: str,
    portrait_locked: bool,
    landscape_locked: bool,
    portrait_filter: str = "",
    landscape_filter: str = "",
) -> tuple[HudPanel, HudPanel]:
    """Both satellites' HUD panels, indexing each side from its own sources.

    The group index is built (and cached) per side exactly as ``_cycle_variant``
    does, so the siblings shown match what cycling would actually reach.
    """
    return (
        _side_panel(
            config, "portrait", config.portrait_sources,
            portrait_current, portrait_locked, portrait_filter,
        ),
        _side_panel(
            config, "landscape", config.landscape_sources,
            landscape_current, landscape_locked, landscape_filter,
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
