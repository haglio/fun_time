"""The buttons Fun Time puts on a satellite's HUD, declared in
:class:`player_core.hud_button.Button` off the player's own state.  Each verb is
the dashboard command the dispatcher answers for that player; a mode button's
names no player, the mode belonging to both satellites at once."""
from __future__ import annotations

from player_core.hud_button import FIT_THE_WORD, Button
from player_core.hud_marks import FMODE_ICON
from player_core.modes import SatellitesMode

from .crown import CROWN_ICON, Crown
from .player_buttons import (
    SATELLITE_NOUN,
    TRASH_ICON,
    browse_order_buttons,
    lock_button,
    minimize_button,
    reset_button,
    transport,
    versions_button,
)

MODE_BUTTONS = (
    ("satellites_kino_activate", "Kino", SatellitesMode.KINO),
    ("origenerator_activate", "Origenerator", SatellitesMode.ORIGENERATOR),
)

MODE_TOOLTIPS = {
    "satellites_kino_activate": "Kino mode — the satellite players and the Random Favs Browser",
    "origenerator_activate":
        "Origenerator mode — Origenerator over the browser, its shows over the players",
}
# The hover a dim Origenerator button gives instead: the room opens without
# waiting out that app's boot, and a hover over a button that cannot be pressed
# has to say why.
STILL_STARTING_TOOLTIP = "Origenerator is still starting — this lights up when it is ready"


def player_rows(player: str, *, locked: bool = False, favorites_filter: bool = False,
                latest: bool | None = None,
                satellites_mode: SatellitesMode | None = None,
                origenerator_ready: bool = True,
                nothing_to_reset: bool = False,
                has_other_versions: bool = False,
                longer_than_a_step: bool = False,
                in_vr: bool = False,
                crowned: bool = False) -> tuple[tuple[Button, ...], ...]:
    rows: list[tuple[Button, ...]] = []
    if satellites_mode is not None:
        rows.append(mode_row(player, satellites_mode=satellites_mode,
                             origenerator_ready=origenerator_ready, in_vr=in_vr,
                             crowned=crowned))
    rows.append((
        *transport(player, noun=SATELLITE_NOUN, longer_than_a_step=longer_than_a_step),
        lock_button(player, locked=locked, noun=SATELLITE_NOUN),
        Button(f"{player}_trash", TRASH_ICON,
               "Unfavorite it — or mark weird when it is not a favorite", danger=True),
        Button(f"{player}_fmode", FMODE_ICON,
               "F-Mode — browse only the favorites on this player",
               lit=favorites_filter, favorite=True),
        reset_button(player, nothing_to_reset=nothing_to_reset),
        *browse_order_buttons(player, latest=latest),
        versions_button(f"{player}_cycle_version", noun=SATELLITE_NOUN,
                        has_other_versions=has_other_versions),
        *(() if in_vr or satellites_mode is not None
          else (minimize_button(player), *_crown(player, crowned))),
    ))
    return tuple(rows)


def mode_row(player: str, *, satellites_mode: SatellitesMode,
             origenerator_ready: bool = True, in_vr: bool = False,
             crowned: bool = False) -> tuple[Button, ...]:
    """The session's own row over a player: the mode pair, and minimize outside the headset."""
    return (
        *(_mode_button(command, label, lit=satellites_mode is lit_mode,
                       dim=command == "origenerator_activate" and not origenerator_ready)
          for command, label, lit_mode in MODE_BUTTONS),
        *(() if in_vr else (minimize_button(player), *_crown(player, crowned))),
    )


def _crown(player: str, crowned: bool) -> tuple[Button, ...]:
    if player != Crown.PORTRAIT:
        return ()
    return (Button(Crown.PORTRAIT.command, CROWN_ICON,
                   "Crowned — this player keeps most of the secondary monitor" if crowned
                   else "Give this player the crown — it keeps most of the secondary "
                   "monitor, whatever the main player shows", lit=crowned),)


def _mode_button(command: str, label: str, *, lit: bool, dim: bool) -> Button:
    return Button(command, label, STILL_STARTING_TOOLTIP if dim else MODE_TOOLTIPS[command],
                  width=FIT_THE_WORD, lit=lit, dim=dim)
