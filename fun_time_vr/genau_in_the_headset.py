"""Genau as it runs on the headset's Main Funestra: the desktop's own, browsing
its VR flicks with the desktop's flat ones, narrowed to a shape when the room
asks, and naming the flick still decoding on the panel as it always has there."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from funestra.console_hud import ModeHud
from funestra.flick_folder import flat_flicks_in, vr_flicks_in

from fun_time.vr_videos import keep_shapes
from main_funestra.genau import Genau


class GenauInTheHeadset(Genau):
    def __init__(self, *, flicks_folder: Path, **genaus_own) -> None:
        self._vr_dirs = (vr_flicks_in(Path(flicks_folder)),)
        self._flicks_dirs = (*self._vr_dirs, flat_flicks_in(Path(flicks_folder)))
        self._shapes = (True, True)
        super().__init__(flicks_folder=flicks_folder, narrow=self._narrow, **genaus_own)

    def top_block(self) -> ModeHud:
        desktops = super().top_block()
        return replace(desktops, video=self._loading or desktops.video)

    def _folders(self) -> tuple[Path, ...]:
        return self._flicks_dirs

    def _shaped(self, flicks: list[Path]) -> list[Path]:
        plays_vr, plays_flat = self._shapes
        return keep_shapes(flicks, vr_dirs=self._vr_dirs, plays_vr=plays_vr, plays_flat=plays_flat)

    def _narrow(self, plays_vr: bool, plays_flat: bool) -> list[Path]:
        self._shapes = (plays_vr, plays_flat)
        return self._scan()
