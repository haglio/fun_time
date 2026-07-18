"""Publish each satellite's lock HUD to the file its player renders from.

The HUD's *model* has to live here: only fun_time knows the library's seed
families, action groups and thumbnails.  The *drawing* lives in the satellite
player, which composites it straight into the video with mpv — so the HUD has no
window of its own and no z-order to fight (see ``satellite.hud`` in genau).

This module is the seam between the two: it turns a :class:`~fun_time.lock_hud.HudPanel`
into the small JSON payload the player parses, and writes it only when it
actually changed, so a player polling the file re-renders per clip change rather
than per tick.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .lock_hud import ACTION_LIMIT, SEED_LIMIT, HudPanel, locate_cell, panel_thumbnails
from .thumbnail_cache import cached_thumbnail

HUD_FILENAME = {"portrait": "portrait_hud.json", "landscape": "landscape_hud.json"}


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
        {"path": path, "thumb": str(thumb), **({"label": by_path[path]} if by_path.get(path) else {})}
        for path, thumb in panel_thumbnails(
            paths, cache_dir, limit=limit, thumbnailer=cached_thumbnail)
    ]


def hud_payload(panel: HudPanel, cache_dir: Path) -> dict:
    """*panel* as the JSON-able payload the satellite player renders.

    Thumbnails are resolved here, so the player never touches the library: it gets
    image paths and draws them.  ``playing`` is resolved to a map *cell* against the
    siblings that actually made it onto the map, so the player lights exactly the
    thumbnail it drew.
    """
    seeds = _cells(panel.seed_siblings, cache_dir, limit=SEED_LIMIT)
    actions = _cells(panel.action_siblings, cache_dir, limit=ACTION_LIMIT,
                     labels=panel.action_labels)
    corner = None
    if panel.current:
        thumb = cached_thumbnail(panel.current, cache_dir)
        corner = {"path": panel.current, "thumb": str(thumb) if thumb else ""}
    playing = locate_cell(
        panel.playing, panel.current,
        [cell["path"] for cell in seeds], [cell["path"] for cell in actions],
    ) or ("corner", 0)
    return {
        "side": panel.side,
        "locked": panel.locked,
        "lock_label": panel.lock_label,
        "filter_query": panel.filter_query,
        "active_loop": panel.active_loop,
        "current_action": panel.current_action,
        "playing": list(playing),
        "corner": corner,
        "seeds": seeds,
        "actions": actions,
    }


def _replace_past_the_players_poll(
    tmp: Path, path: Path, *, attempts: int = 5, delay_s: float = 0.005,
) -> bool:
    """Rename *tmp* over *path*, retrying past the player's read of it.

    The satellite polls its HUD file every frame, and Windows refuses to replace
    a file another process holds open — so a publish landing inside one of those
    reads fails with a sharing violation.  Retrying turns that into a
    sub-millisecond wait; a file locked for longer than that gives up and reports
    it, so the caller republishes rather than believing a lost panel was
    delivered.  (The player's own click-command writes use the same idiom.)
    """
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return True
        except OSError:
            if attempt < attempts - 1:
                time.sleep(delay_s)
    return False


class HudPublisher:
    """Writes both satellites' HUD files, skipping unchanged panels."""

    def __init__(self, files: dict[str, Path], cache_dir: Path) -> None:
        self._files = files
        self._cache_dir = cache_dir
        self._last: dict[str, str] = {}

    def publish(self, side: str, panel: HudPanel) -> bool:
        """Write *side*'s HUD file if the panel changed; return whether it wrote."""
        path = self._files.get(side)
        if path is None:
            return False
        text = json.dumps(hud_payload(panel, self._cache_dir))
        if text == self._last.get(side):
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        # Written aside and renamed over: the player polls this file, and an
        # atomic replace means it always reads a whole panel, never a half-written
        # one.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        if not _replace_past_the_players_poll(tmp, path):
            return False
        # Remembered only once the panel is actually on disk: a write that never
        # landed must be retried on the next tick, not treated as published.
        self._last[side] = text
        return True
