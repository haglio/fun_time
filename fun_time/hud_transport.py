"""Publish each Funestra's HUD to the file that Funestra renders from.

The HUD's *model* has to live here: only fun_time knows the library's seed
families, action groups and thumbnails.  The *drawing* lives in the Satellite
Funestra, which composites it straight into the video with mpv — so the HUD has no
window of its own and no z-order to fight (see :mod:`funestra.satellite_hud`).

This module is the seam between the two: it turns a :class:`~fun_time.lock_hud.HudPanel`
into the :class:`~funestra.satellite_hud.HudModel` the Funestra parses back, and
writes its text only when it actually changed, so a Funestra polling the file
re-renders per clip change rather than per tick.  The Main Funestra's console
rides the same publisher, and so does a side the hosted Origenerator holds —
whose panel is that app's own, under the session's row.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from funestra.file_channel import publish_whole
from funestra.hud_placement import HudCorner, HudEdge
from funestra.satellite_hud import HudCell, HudModel, hud_text

from .folder_listings import FolderListings
from .lock_hud import ACTION_LIMIT, SEED_LIMIT, HudPanel, locate_cell, panel_thumbnails
from .origenerator_switches import ORIGENERATOR_MODE
from .osr2_section import DeviceBlock
from .satellite_buttons import funestra_rows, mode_row
from .thumbnail_cache import cached_thumbnail

# What a side the hosted app holds says before that app has anything on it.
ORIGENERATOR_MODE_LABEL = "Origenerator mode"


def _cell(path: str, thumb: object, label: str = "") -> HudCell:
    """One map cell as the Funestra draws it; an absent thumbnail draws a placeholder."""
    return HudCell(path=path, thumb=str(thumb) if thumb else "", label=label)


def _cells(paths: list[str], cache_dir: Path, listings: FolderListings, *, limit: int,
           labels: tuple[str, ...] = ()) -> tuple[HudCell, ...]:
    """The drawable siblings: up to *limit* clips whose thumbnail is already cached.

    Cached-only, never extracting: a cv2 frame grab takes seconds on HEVC, and the
    map has to keep up with ~5 s clips.  A sibling whose frame the background
    prewarm has not produced yet is simply not on the map this publish, and appears
    on a later one.
    """
    by_path = dict(zip(paths, labels))
    return tuple(
        _cell(path, thumb, by_path.get(path, ""))
        for path, thumb in panel_thumbnails(
            paths, cache_dir, limit=limit,
            thumbnailer=lambda path, cache: cached_thumbnail(path, cache, listings))
    )


def _loop_cells(paths: list[str], cache_dir: Path, listings: FolderListings,
                labels: tuple[str, ...] = ()) -> tuple[HudCell, ...]:
    """Every clip a running loop cycles, in the loop's own order.

    Neither capped nor cached-only, unlike the browse map: the Funestra windows this
    list around the clip on screen, so it needs the whole loop to window over, and
    an item whose frame is not ready yet has to hold its place (as a placeholder)
    rather than renumber the cells after it and slide the window off the clip
    playing.
    """
    by_path = dict(zip(paths, labels))
    return tuple(_cell(path, cached_thumbnail(path, cache_dir, listings), by_path.get(path, ""))
                 for path in paths)


def hud_model(panel: HudPanel, cache_dir: Path,
              camera_words: tuple[str, ...] = ()) -> HudModel:
    """*panel* as the record the Satellite Funestra renders.

    Thumbnails are resolved here, so the Funestra never touches the library: it gets
    image paths and draws them.  ``playing`` is resolved to a map *cell* against the
    siblings that actually made it onto the map, so the Funestra lights exactly the
    thumbnail it drew.
    """
    listings = FolderListings()
    # The looped axis is published whole — the Funestra windows it around the clip on
    # screen; the other axis is the ordinary browse map, drawn to its cap.
    seeds = (
        _loop_cells(panel.seed_siblings, cache_dir, listings) if panel.active_loop == "seed"
        else _cells(panel.seed_siblings, cache_dir, listings, limit=SEED_LIMIT)
    )
    actions = (
        _loop_cells(panel.action_siblings, cache_dir, listings, panel.action_labels)
        if panel.active_loop == "action"
        else _cells(panel.action_siblings, cache_dir, listings, limit=ACTION_LIMIT,
                    labels=panel.action_labels)
    )
    corner = None
    if panel.current:
        corner = _cell(panel.current, cached_thumbnail(panel.current, cache_dir, listings))
    playing = locate_cell(
        panel.playing, panel.current,
        [cell.path for cell in seeds], [cell.path for cell in actions],
    ) or ("corner", 0)
    return HudModel(
        funestra=panel.funestra,
        hud_corner=panel.hud_corner,
        hud_edge=panel.hud_edge,
        hud_minimized=panel.hud_minimized,
        locked=panel.locked,
        title=panel.title,
        active=panel.active,
        is_favorite=panel.is_favorite,
        rows=funestra_rows(panel.funestra, locked=panel.locked,
                         favorites_filter=panel.favorites_filter, latest=panel.latest,
                         mode=panel.mode,
                         origenerator_ready=panel.origenerator_ready,
                         nothing_to_reset=panel.nothing_to_reset,
                         has_other_versions=panel.has_other_versions,
                         longer_than_a_step=panel.longer_than_a_step,
                         in_vr=panel.in_vr, crowned=panel.crowned),
        filter_query=panel.filter_query,
        camera_words=camera_words,
        seed_count=panel.seed_count,
        action_count=panel.action_count,
        active_loop=panel.active_loop,
        current_action=panel.current_action,
        playing=playing,
        corner=corner,
        seeds=seeds,
        actions=actions,
    )


def hosted_model(funestra: str, hosted: HudModel | None, *, active: bool,
                 origenerator_ready: bool, in_vr: bool, crowned: bool = False,
                 device: DeviceBlock | None = None,
                 hud_corner: HudCorner = HudCorner.UPPER_LEFT,
                 hud_edge: HudEdge = HudEdge.LOWER,
                 hud_minimized: bool = False) -> HudModel:
    """What *Funestra* wears while the hosted app holds its Funestra: that app's own
    panel, or the mode's name while it has none, under the session's row."""
    panel = hosted or HudModel(funestra=funestra, title=ORIGENERATOR_MODE_LABEL)
    row = mode_row(funestra, mode=ORIGENERATOR_MODE,
                   origenerator_ready=origenerator_ready, in_vr=in_vr, crowned=crowned)
    model = replace(panel, funestra=funestra, active=active, hud_corner=hud_corner,
                    hud_edge=hud_edge, hud_minimized=hud_minimized,
                    rows=(row, *panel.rows))
    return model if device is None else device.worn_by(model)


class HudPublisher:
    """Writes each Funestra's HUD file, skipping unchanged panels.

    Named for what it publishes to rather than what it publishes: the satellites
    take a map of clips and the Main Funestra takes its console, and both want the same
    write-only-on-change, publish-whole treatment.
    """

    def __init__(self, files: dict[str, Path], cache_dir: Path,
                 camera_words: tuple[str, ...]) -> None:
        self._files = files
        self._cache_dir = cache_dir
        self._camera_words = camera_words
        self._last: dict[str, str] = {}

    def publish(self, funestra: str, panel: HudPanel,
                device: DeviceBlock | None = None) -> bool:
        """Write *Funestra*'s map if the panel changed; return whether it wrote."""
        model = hud_model(panel, self._cache_dir, self._camera_words)
        return self.publish_text(
            funestra, hud_text(model if device is None else device.worn_by(model)))

    def publish_text(self, name: str, text: str) -> bool:
        """Write *name*'s HUD file if *text* changed; return whether it wrote."""
        path = self._files.get(name)
        if path is None:
            return False
        if text == self._last.get(name):
            return False
        # Published whole: the Funestra polls this file every frame, so a panel
        # written in place would sometimes be read half-drawn.
        if not publish_whole(path, text):
            return False
        # Remembered only once the panel is actually on disk: a write that never
        # landed must be retried on the next tick, not treated as published.
        self._last[name] = text
        return True
