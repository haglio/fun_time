from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

from .hud_overlay import HUD_OVERLAY_ID

FRAME_OVERLAY_ID = HUD_OVERLAY_ID - 1


def fitted_bgra(frame: Path, width: int, height: int) -> np.ndarray | None:
    try:
        with Image.open(frame) as opened:
            picture = opened.convert("RGB")
    except (OSError, UnidentifiedImageError, ValueError):
        return None
    scale = min(width / picture.width, height / picture.height)
    fitted = picture.resize((max(1, round(picture.width * scale)),
                             max(1, round(picture.height * scale))))
    canvas = Image.new("RGB", (width, height))
    canvas.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    rgba = np.asarray(canvas.convert("RGBA"))
    return np.ascontiguousarray(rgba[:, :, [2, 1, 0, 3]])


class FrameOver:
    def __init__(self, player, overlay_id: int = FRAME_OVERLAY_ID) -> None:
        self._player = player
        self._overlay_id = overlay_id
        self._painted: tuple | None = None

    def paint(self, frame: Path | None, width: int, height: int) -> None:
        wanted = None if frame is None else (frame, width, height)
        if wanted == self._painted:
            return
        if wanted is None:
            self._player.remove_overlay(self._overlay_id)
            self._painted = None
            return
        bgra = fitted_bgra(frame, width, height)
        if bgra is None:
            return
        self._player.overlay(self._overlay_id, 0, 0, bgra)
        self._painted = wanted
