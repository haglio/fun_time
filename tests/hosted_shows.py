from __future__ import annotations

from funestra.hud_button import Button
from funestra.hud_marks import FMODE_ICON
from funestra.hud_status import F_MODE_LABEL
from funestra.satellite_hud import HudModel, hud_text

from fun_time.bridge_records import BridgeConfig
from fun_time.room_funestras import RoomFunestra


def publish_the_show(config: BridgeConfig, funestra: RoomFunestra, *, f_mode: bool,
                     enhanced: bool | None = None) -> None:
    """The hosted app's panel for *funestra*'s side, with its F-mode switch
    and, where the show draws one, its enhanced-only switch."""
    buttons = [Button(f"{funestra.label}_fmode", FMODE_ICON, F_MODE_LABEL, lit=f_mode)]
    if enhanced is not None:
        buttons.append(Button(f"{funestra.label}_enhanced", "E", "Enhanced only", lit=enhanced))
    panel = HudModel(funestra=funestra.label, rows=(tuple(buttons),))
    config.satellite(funestra).origenerator_hud_file.write_text(hud_text(panel), encoding="utf-8")
