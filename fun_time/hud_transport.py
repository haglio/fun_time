"""Publish each player's HUD to the file that player renders from.

The HUD's *model* has to live here: only fun_time knows the library's seed
families, action groups and thumbnails.  The *drawing* lives in the satellite
player, which composites it straight into the video with mpv — so the HUD has no
window of its own and no z-order to fight (see :mod:`player_core.satellite_hud`).

This module is the seam between the two: it turns a :class:`~fun_time.lock_hud.HudPanel`
into the small JSON payload the player parses, and writes it only when it
actually changed, so a player polling the file re-renders per clip change rather
than per tick.  Nau's console (:mod:`fun_time.nau_console`) rides the same
publisher — a different panel, the same "write it whole, and only when it moved".
"""
from __future__ import annotations

import json
from pathlib import Path

from player_core.file_channel import publish_whole

from .lock_hud import ACTION_LIMIT, SEED_LIMIT, HudPanel, locate_cell, panel_thumbnails
from .thumbnail_cache import cached_thumbnail

HUD_FILENAME = {"portrait": "portrait_hud.json", "landscape": "landscape_hud.json"}


def _cell(path: str, thumb: object, label: str = "") -> dict[str, str]:
    """One map cell as the player parses it; an absent thumbnail draws a placeholder."""
    cell = {"path": path, "thumb": str(thumb) if thumb else ""}
    if label:
        cell["label"] = label
    return cell


def _cells(paths: list[str], cache_dir: Path, *, limit: int,
           labels: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """The drawable siblings: up to *limit* clips whose thumbnail is already cached.

    Cached-only, never extracting: a cv2 frame grab takes seconds on HEVC, and the
    map has to keep up with ~5 s clips.  A sibling whose frame the background
    prewarm has not produced yet is simply not on the map this publish, and appears
    on a later one.
    """
    by_path = dict(zip(paths, labels))
    return [
        _cell(path, thumb, by_path.get(path, ""))
        for path, thumb in panel_thumbnails(
            paths, cache_dir, limit=limit, thumbnailer=cached_thumbnail)
    ]


def _loop_cells(paths: list[str], cache_dir: Path,
                labels: tuple[str, ...] = ()) -> list[dict[str, str]]:
    """Every clip a running loop cycles, in the loop's own order.

    Neither capped nor cached-only, unlike the browse map: the player windows this
    list around the clip on screen, so it needs the whole loop to window over, and
    a member whose frame is not ready yet has to hold its place (as a placeholder)
    rather than renumber the cells behind it and slide the window off the clip
    playing.
    """
    by_path = dict(zip(paths, labels))
    return [_cell(path, cached_thumbnail(path, cache_dir), by_path.get(path, "")) for path in paths]


def hud_payload(panel: HudPanel, cache_dir: Path) -> dict:
    """*panel* as the JSON-able payload the satellite player renders.

    Thumbnails are resolved here, so the player never touches the library: it gets
    image paths and draws them.  ``playing`` is resolved to a map *cell* against the
    siblings that actually made it onto the map, so the player lights exactly the
    thumbnail it drew.
    """
    # The looped axis is published whole — the player windows it around the clip on
    # screen; the other axis is the ordinary browse map, drawn to its cap.
    seeds = (
        _loop_cells(panel.seed_siblings, cache_dir) if panel.active_loop == "seed"
        else _cells(panel.seed_siblings, cache_dir, limit=SEED_LIMIT)
    )
    actions = (
        _loop_cells(panel.action_siblings, cache_dir, panel.action_labels)
        if panel.active_loop == "action"
        else _cells(panel.action_siblings, cache_dir, limit=ACTION_LIMIT,
                    labels=panel.action_labels)
    )
    corner = None
    if panel.current:
        corner = _cell(panel.current, cached_thumbnail(panel.current, cache_dir))
    playing = locate_cell(
        panel.playing, panel.current,
        [cell["path"] for cell in seeds], [cell["path"] for cell in actions],
    ) or ("corner", 0)
    return {
        "side": panel.side,
        "locked": panel.locked,
        "lock_label": panel.lock_label,
        "active": panel.active,
        "satellites_mode": panel.satellites_mode,

        "is_favorite": panel.is_favorite,
        "f_mode": panel.f_mode,
        "filter_query": panel.filter_query,
        "seed_count": panel.seed_count,
        "action_count": panel.action_count,
        "active_loop": panel.active_loop,
        "current_action": panel.current_action,
        "playing": list(playing),
        "corner": corner,
        "seeds": seeds,
        "actions": actions,
    }


class HudPublisher:
    """Writes each player's HUD file, skipping unchanged panels.

    Named for what it publishes to rather than what it publishes: the satellites
    take a map of clips and Nau takes its console, and both want the same
    write-only-on-change, publish-whole treatment.
    """

    def __init__(self, files: dict[str, Path], cache_dir: Path) -> None:
        self._files = files
        self._cache_dir = cache_dir
        self._last: dict[str, str] = {}

    def publish(self, side: str, panel: HudPanel) -> bool:
        """Write *side*'s map if the panel changed; return whether it wrote."""
        return self.publish_payload(side, hud_payload(panel, self._cache_dir))

    def publish_payload(self, name: str, payload: dict) -> bool:
        """Write *name*'s HUD file if *payload* changed; return whether it wrote."""
        path = self._files.get(name)
        if path is None:
            return False
        text = json.dumps(payload)
        if text == self._last.get(name):
            return False
        # Published whole: the player polls this file every frame, so a panel
        # written in place would sometimes be read half-drawn.
        if not publish_whole(path, text):
            return False
        # Remembered only once the panel is actually on disk: a write that never
        # landed must be retried on the next tick, not treated as published.
        self._last[name] = text
        return True
