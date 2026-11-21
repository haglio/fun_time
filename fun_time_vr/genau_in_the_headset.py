from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from funestra.flick_folder import flat_flicks_in, vr_flicks_in
from funestra.genau import Genau
from funestra.main_hud import ModeHud

from fun_time import vr_videos
from main_funestra.library_flicks import LibraryFlicks


class GenauInTheHeadset(Genau):
    def top_block(self) -> ModeHud:
        desktops = super().top_block()
        return replace(desktops, video=self.loading or desktops.video)


class FlicksInTheHeadset(LibraryFlicks):
    def __init__(self, folder: Path, **asked) -> None:
        super().__init__(folder, **asked)
        self._vr_dirs = (vr_flicks_in(Path(folder)),)
        self._flicks_dirs = (*self._vr_dirs, flat_flicks_in(Path(folder)))
        self._shapes = (True, True)

    def keep_shapes(self, plays_vr: bool, plays_flat: bool) -> None:
        self._shapes = (plays_vr, plays_flat)

    def _folders(self) -> tuple[Path, ...]:
        return self._flicks_dirs

    def _shaped(self, flicks: list[Path]) -> list[Path]:
        plays_vr, plays_flat = self._shapes
        return vr_videos.keep_shapes(flicks, vr_dirs=self._vr_dirs, plays_vr=plays_vr, plays_flat=plays_flat)
