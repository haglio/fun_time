from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from funestra.file_channel import publish_whole
from funestra.satellite_hud import HudCell, HudModel, hud_text

from .folder_listings import FolderListings
from .lock_hud import ACTION_LIMIT, SEED_LIMIT, SatelliteHud, hud_thumbnails, locate_cell
from .origenerator_switches import ORIGENERATOR_MODE
from .osr2_section import DeviceBlock
from .satellite_buttons import HudState, funestra_rows, mode_row
from .shared_state import SatelliteState
from .thumbnail_cache import cached_thumbnail

ORIGENERATOR_MODE_LABEL = "Origenerator mode"


def _cell(path: str, thumb: object, label: str = "") -> HudCell:
    return HudCell(path=path, thumb=str(thumb) if thumb else "", label=label)


def _cells(paths: list[str], cache_dir: Path, listings: FolderListings, *, limit: int,
           lines: tuple[str, ...] = ()) -> tuple[HudCell, ...]:
    by_path = dict(zip(paths, lines, strict=False))
    return tuple(
        _cell(path, thumb, by_path.get(path, ""))
        for path, thumb in hud_thumbnails(
            paths, cache_dir, limit=limit,
            thumbnailer=lambda path, cache: cached_thumbnail(path, cache, listings))
    )


def _whole_loop_for_the_funestra_to_window(
        paths: list[str], cache_dir: Path, listings: FolderListings,
        lines: tuple[str, ...] = ()) -> tuple[HudCell, ...]:
    by_path = dict(zip(paths, lines, strict=False))
    return tuple(_cell(path, cached_thumbnail(path, cache_dir, listings), by_path.get(path, ""))
                 for path in paths)


def hud_model(hud: SatelliteHud, cache_dir: Path) -> HudModel:
    listings = FolderListings()
    seeds = (
        _whole_loop_for_the_funestra_to_window(hud.seed_siblings, cache_dir, listings)
        if hud.active_loop == "seed"
        else _cells(hud.seed_siblings, cache_dir, listings, limit=SEED_LIMIT)
    )
    actions = (
        _whole_loop_for_the_funestra_to_window(
            hud.action_siblings, cache_dir, listings, hud.action_lines)
        if hud.active_loop == "action"
        else _cells(hud.action_siblings, cache_dir, listings, limit=ACTION_LIMIT,
                    lines=hud.action_lines)
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
        rows=funestra_rows(hud.funestra, HudState.of(hud)),
        filter_query=hud.filter_query,
        seed_count=hud.seed_count,
        action_count=hud.action_count,
        active_loop=hud.active_loop,
        current_action=hud.current_action,
        playing=playing,
        corner=corner,
        seeds=seeds,
        actions=actions,
    )


def worn(model: HudModel, device: DeviceBlock | None) -> HudModel:
    return model if device is None else device.worn_by(model)


def hosted_model(funestra: str, hosted: HudModel | None, held: SatelliteState,
                 state: HudState, *, active: bool) -> HudModel:
    hud = hosted or HudModel(funestra=funestra, title=ORIGENERATOR_MODE_LABEL)
    row = mode_row(funestra, mode=ORIGENERATOR_MODE,
                   origenerator_ready=state.origenerator_ready, in_vr=state.in_vr,
                   crowned=state.crowned)
    return replace(hud, funestra=funestra, active=active, hud_corner=held.hud_corner,
                   hud_edge=held.hud_edge, hud_minimized=held.hud_minimized,
                   rows=(row, *hud.rows))


class HudPublisher:
    def __init__(self, files: dict[str, Path], cache_dir: Path) -> None:
        self._files = files
        self._cache_dir = cache_dir
        self._landed: dict[str, str] = {}

    def publish(self, funestra: str, hud: SatelliteHud,
                device: DeviceBlock | None = None) -> bool:
        model = hud_model(hud, self._cache_dir)
        return self.publish_text(funestra, hud_text(worn(model, device)))

    def publish_text(self, name: str, text: str) -> bool:
        path = self._files.get(name)
        if path is None or self._would_only_make_the_funestra_redraw(name, text):
            return False
        if not publish_whole(path, text):
            return False
        self._landed[name] = text
        return True

    def _would_only_make_the_funestra_redraw(self, name: str, text: str) -> bool:
        return text == self._landed.get(name)
