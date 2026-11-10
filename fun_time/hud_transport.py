from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from funestra.file_channel import publish_whole
from funestra.satellite_hud import HudModel

from .osr2_section import DeviceBlock
from .satellite_buttons import window_buttons
from .shared_state import SatelliteState

ORIGENERATOR_MODE_LABEL = "Origenerator mode"


def worn(model: HudModel, device: DeviceBlock | None) -> HudModel:
    return model if device is None else device.worn_by(model)


def hosted_model(funestra: str, hosted: HudModel | None, held: SatelliteState, *,
                 in_vr: bool, crowned: bool, active: bool) -> HudModel:
    hud = hosted or HudModel(funestra=funestra, title=ORIGENERATOR_MODE_LABEL)
    own = window_buttons(funestra, in_vr=in_vr, crowned=crowned)
    return replace(hud, funestra=funestra, active=active, hud_corner=held.hud_corner,
                   hud_edge=held.hud_edge, hud_minimized=held.hud_minimized,
                   rows=(*((own,) if own else ()), *hud.rows))


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
