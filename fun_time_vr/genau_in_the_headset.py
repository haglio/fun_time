"""Genau as it runs on the headset's Main Funestra: the desktop's own, browsing
its VR clips with the desktop's flat ones, narrowed to a shape when the room
asks, and naming the clip still decoding on the panel as it always has there."""
from __future__ import annotations

from pathlib import Path

from player_core.clip_folder import flat_clips_in, scan_clips, vr_clips_in
from player_core.console_hud import ModeHud

from fun_time.vr_videos import keep_shapes
from main_player.genau import Genau


class GenauInTheHeadset(Genau):
    def __init__(self, *, clips_folder: Path, **genaus_own) -> None:
        self._vr_dirs = (vr_clips_in(Path(clips_folder)),)
        self._clips_dirs = (*self._vr_dirs, flat_clips_in(Path(clips_folder)))
        self._shapes = (True, True)
        super().__init__(clips_folder=clips_folder, narrow=self._narrow, **genaus_own)

    def top_block(self) -> ModeHud:
        return ModeHud(video=self._loading or super().top_block().video)

    def _scan(self) -> list[Path]:
        plays_vr, plays_flat = self._shapes
        return keep_shapes(
            scan_clips(self._clips_dirs, shuffle_on_load=self._settings.shuffle_on_load,
                       recent=self._recent),
            vr_dirs=self._vr_dirs, plays_vr=plays_vr, plays_flat=plays_flat)

    def _narrow(self, plays_vr: bool, plays_flat: bool) -> list[Path]:
        self._shapes = (plays_vr, plays_flat)
        return self._scan()
