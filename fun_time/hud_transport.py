"""Publish each Funestra's HUD to the file that Funestra renders from.

The HUD's *model* has to live here: only fun_time knows the library's seed
families, action groups and thumbnails.  The *drawing* lives in the Satellite
Funestra, which composites it straight into the video with mpv — so the HUD has no
window of its own and no z-order to fight (see :mod:`funestra.satellite_hud`).

This module is the seam between the two: it turns a :class:`~fun_time.lock_hud.SatelliteHud`
into the :class:`~funestra.satellite_hud.HudModel` the Funestra parses back, and
writes its text only when it actually changed, so a Funestra polling the file
re-renders per video change rather than per tick.  The Main Funestra's console
rides the same publisher, and so does a Satellite the hosted Origenerator holds —
whose HUD is that app's own, under the session's row.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from funestra.file_channel import publish_whole
from funestra.hud_placement import HudCorner, HudEdge
from funestra.satellite_hud import HudCell, HudModel, hud_text

from .folder_listings import FolderListings
from .lock_hud import ACTION_LIMIT, SEED_LIMIT, SatelliteHud, hud_thumbnails, locate_cell
from .origenerator_switches import ORIGENERATOR_MODE
from .osr2_section import DeviceBlock
from .satellite_buttons import funestra_rows, mode_row
from .thumbnail_cache import cached_thumbnail

ORIGENERATOR_MODE_LABEL = "Origenerator mode"


def _cell(path: str, thumb: object, label: str = "") -> HudCell:
    return HudCell(path=path, thumb=str(thumb) if thumb else "", label=label)


def _cells(paths: list[str], cache_dir: Path, listings: FolderListings, *, limit: int,
           labels: tuple[str, ...] = ()) -> tuple[HudCell, ...]:
    by_path = dict(zip(paths, labels, strict=False))
    return tuple(
        _cell(path, thumb, by_path.get(path, ""))
        for path, thumb in hud_thumbnails(
            paths, cache_dir, limit=limit,
            thumbnailer=lambda path, cache: cached_thumbnail(path, cache, listings))
    )


def _loop_cells(paths: list[str], cache_dir: Path, listings: FolderListings,
                labels: tuple[str, ...] = ()) -> tuple[HudCell, ...]:
    by_path = dict(zip(paths, labels, strict=False))
    return tuple(_cell(path, cached_thumbnail(path, cache_dir, listings), by_path.get(path, ""))
                 for path in paths)


def hud_model(hud: SatelliteHud, cache_dir: Path,
              camera_words: tuple[str, ...] = ()) -> HudModel:
    listings = FolderListings()
    seeds = (
        _loop_cells(hud.seed_siblings, cache_dir, listings) if hud.active_loop == "seed"
        else _cells(hud.seed_siblings, cache_dir, listings, limit=SEED_LIMIT)
    )
    actions = (
        _loop_cells(hud.action_siblings, cache_dir, listings, hud.action_labels)
        if hud.active_loop == "action"
        else _cells(hud.action_siblings, cache_dir, listings, limit=ACTION_LIMIT,
                    labels=hud.action_labels)
    )
    corner = None
    if hud.current:
        corner = _cell(hud.current, cached_thumbnail(hud.current, cache_dir, listings))
    playing = locate_cell(
        hud.playing, hud.current,
        [cell.path for cell in seeds], [cell.path for cell in actions],
    ) or ("corner", 0)
    return HudModel(
        funestra=hud.funestra,
        hud_corner=hud.hud_corner,
        hud_edge=hud.hud_edge,
        hud_minimized=hud.hud_minimized,
        locked=hud.locked,
        title=hud.title,
        active=hud.active,
        is_favorite=hud.is_favorite,
        rows=funestra_rows(hud.funestra, locked=hud.locked,
                         favorites_filter=hud.favorites_filter, latest=hud.latest,
                         mode=hud.mode,
                         origenerator_ready=hud.origenerator_ready,
                         nothing_to_reset=hud.nothing_to_reset,
                         has_other_versions=hud.has_other_versions,
                         longer_than_a_step=hud.longer_than_a_step,
                         in_vr=hud.in_vr, crowned=hud.crowned,
                         has_osr2=hud.has_osr2, has_funscript=hud.has_funscript),
        filter_query=hud.filter_query,
        camera_words=camera_words,
        seed_count=hud.seed_count,
        action_count=hud.action_count,
        active_loop=hud.active_loop,
        current_action=hud.current_action,
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
    hud = hosted or HudModel(funestra=funestra, title=ORIGENERATOR_MODE_LABEL)
    row = mode_row(funestra, mode=ORIGENERATOR_MODE,
                   origenerator_ready=origenerator_ready, in_vr=in_vr, crowned=crowned)
    model = replace(hud, funestra=funestra, active=active, hud_corner=hud_corner,
                    hud_edge=hud_edge, hud_minimized=hud_minimized,
                    rows=(row, *hud.rows))
    return model if device is None else device.worn_by(model)


class HudPublisher:
    def __init__(self, files: dict[str, Path], cache_dir: Path,
                 camera_words: tuple[str, ...]) -> None:
        self._files = files
        self._cache_dir = cache_dir
        self._camera_words = camera_words
        self._last: dict[str, str] = {}

    def publish(self, funestra: str, hud: SatelliteHud,
                device: DeviceBlock | None = None) -> bool:
        model = hud_model(hud, self._cache_dir, self._camera_words)
        return self.publish_text(
            funestra, hud_text(model if device is None else device.worn_by(model)))

    def publish_text(self, name: str, text: str) -> bool:
        path = self._files.get(name)
        if path is None:
            return False
        if text == self._last.get(name):
            return False
        if not publish_whole(path, text):
            return False
        self._last[name] = text
        return True
