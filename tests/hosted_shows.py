from __future__ import annotations

from player_core.hud_button import Button
from player_core.hud_marks import FMODE_ICON
from player_core.hud_status import F_MODE_LABEL
from player_core.satellite_hud import HudModel, hud_text

from fun_time.bridge_records import BridgeConfig
from fun_time.players import Player


def publish_the_show(config: BridgeConfig, player: Player, *, f_mode: bool) -> None:
    panel = HudModel(player=player.label, rows=((
        Button(f"{player.label}_fmode", FMODE_ICON, F_MODE_LABEL, lit=f_mode),),))
    config.satellite(player).origenerator_hud_file.write_text(hud_text(panel), encoding="utf-8")
