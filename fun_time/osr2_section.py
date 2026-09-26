from __future__ import annotations

from .players import Player


def take_osr2_command(player: Player) -> str:
    return f"{player.label}_take_osr2"


TAKE_OSR2_COMMANDS: dict[str, Player] = {take_osr2_command(player): player for player in Player}
