from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from funestra.file_channel import publish_whole
from funestra.hud_kinds import HudFacts, HudKind, osr2_button, window_buttons
from funestra.modes import Osr2State
from funestra.satellite_hud import HudModel, parse_hud

from .shared_state import SatelliteState


def read_the_hosted_hud(path: Path) -> HudModel | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    return parse_hud(text)


def the_hosted_slideshow_is_locked(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        hud = read_the_hosted_hud(path)
    except OSError:
        return False
    return hud is not None and hud.locked


def hosted_model(funestra: str, hosted: HudModel | None, held: SatelliteState, *,
                 in_vr: bool, crowned: bool, active: bool, osr2: Osr2State | None) -> HudModel:
    hud = replace(hosted or HudModel(funestra=funestra), funestra=funestra, active=active,
                  hud_corner=held.hud_corner, hud_edge=held.hud_edge,
                  hud_minimized=held.hud_minimized, osr2=osr2 or "")
    if hud.facts is None:
        return _with_the_funestras_own_buttons(hud, in_vr=in_vr, crowned=crowned,
                                               has_osr2=osr2 is not None)
    return replace(hud, kind=HudKind.SATELLITE, osr2_rows=(), facts=replace(
        hud.facts, funestra=funestra, crowned=crowned, has_osr2=osr2 is not None, in_vr=in_vr,
        favorite=hud.is_favorite))


def _with_the_funestras_own_buttons(hud: HudModel, *, in_vr: bool, crowned: bool,
                                    has_osr2: bool) -> HudModel:
    own = window_buttons(HudFacts(funestra=hud.funestra, in_vr=in_vr, crowned=crowned))
    return replace(hud, rows=(*((own,) if own else ()), *hud.rows),
                   osr2_rows=((osr2_button(hud.funestra, has_it=has_osr2),),))


class HudPublisher:
    def __init__(self, files: dict[str, Path]) -> None:
        self._files = files
        self._landed: dict[str, str] = {}

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
