from __future__ import annotations

from funestra_core.hud_button import Button
from funestra_core.hud_marks import FMODE_ICON
from funestra_core.hud_status import F_MODE_LABEL
from funestra_core.satellite_hud import HudModel, hud_text

from fun_time.bridge_records import BridgeConfig
from fun_time.room_funestras import RoomFunestra


def publish_the_show(config: BridgeConfig, funestra: RoomFunestra, *, f_mode: bool) -> None:
    panel = HudModel(funestra=funestra.label, rows=((
        Button(f"{funestra.label}_fmode", FMODE_ICON, F_MODE_LABEL, lit=f_mode),),))
    config.satellite(funestra).origenerator_hud_file.write_text(hud_text(panel), encoding="utf-8")
