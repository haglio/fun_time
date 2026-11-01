from __future__ import annotations

from funestra.hud_button import Button
from funestra.hud_icons import FMODE_ICON
from funestra.hud_title import F_MODE_LABEL
from funestra.satellite_hud import HudModel, hud_text

from fun_time.bridge_records import BridgeConfig
from fun_time.fun_time_funestras import FunTimeFunestra


def publish_the_show(config: BridgeConfig, funestra: FunTimeFunestra, *, f_mode: bool,
                     enhanced: bool | None = None) -> None:
    buttons = [Button(f"{funestra.label}_fmode", FMODE_ICON, F_MODE_LABEL, lit=f_mode)]
    if enhanced is not None:
        buttons.append(Button(f"{funestra.label}_enhanced", "E", "Enhanced only", lit=enhanced))
    hud = HudModel(funestra=funestra.label, rows=(tuple(buttons),))
    config.satellite(funestra).origenerator_hud_file.write_text(hud_text(hud), encoding="utf-8")
