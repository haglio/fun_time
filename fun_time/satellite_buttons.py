"""The buttons Fun Time puts on a satellite's HUD, declared in
:class:`funestra.hud_button.Button` off the Funestra's own state.  Each verb is
the dashboard command the dispatcher answers for that Funestra; a mode button's
names no Funestra, the mode belonging to both satellites at once."""
from __future__ import annotations

from funestra.hud_button import FIT_THE_WORD, Button
from funestra.hud_marks import FMODE_ICON
from funestra.modes import SatellitesMode

from .crown import CROWN_ICON, Crown
from .funestra_buttons import (
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
    "satellites_kino_activate": "Kino mode — the Satellite Funestras and the Random Favs Browser",
    "origenerator_activate":
        "Origenerator mode — Origenerator over the browser, its slideshows over the Funestras",
}
STILL_STARTING_TOOLTIP = ("Origenerator is still starting — this button becomes enabled "
                          "when it's ready")


def funestra_rows(funestra: str, *, locked: bool = False, favorites_filter: bool = False,
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
        rows.append(mode_row(funestra, satellites_mode=satellites_mode,
                             origenerator_ready=origenerator_ready, in_vr=in_vr,
                             crowned=crowned))
    rows.append((
        *transport(funestra, noun=SATELLITE_NOUN, longer_than_a_step=longer_than_a_step),
        lock_button(funestra, locked=locked, noun=SATELLITE_NOUN),
        Button(f"{funestra}_trash", TRASH_ICON,
               "Unfavorite it — or mark weird when it is not a favorite", danger=True),
        Button(f"{funestra}_fmode", FMODE_ICON,
               "F-mode — browse only the favorites on this Funestra",
               lit=favorites_filter, favorite=True),
        reset_button(funestra, nothing_to_reset=nothing_to_reset),
        *browse_order_buttons(funestra, latest=latest),
        versions_button(f"{funestra}_cycle_version", noun=SATELLITE_NOUN,
                        has_other_versions=has_other_versions),
        *(() if in_vr or satellites_mode is not None
          else (minimize_button(funestra), *_crown(funestra, crowned))),
    ))
    return tuple(rows)


def mode_row(funestra: str, *, satellites_mode: SatellitesMode,
             origenerator_ready: bool = True, in_vr: bool = False,
             crowned: bool = False) -> tuple[Button, ...]:
    """The session's own row over a Funestra: the mode pair, and minimize outside the headset."""
    return (
        *(_mode_button(command, label, lit=satellites_mode is lit_mode,
                       dim=command == "origenerator_activate" and not origenerator_ready)
          for command, label, lit_mode in MODE_BUTTONS),
        *(() if in_vr else (minimize_button(funestra), *_crown(funestra, crowned))),
    )


def _crown(funestra: str, crowned: bool) -> tuple[Button, ...]:
    if funestra != Crown.PORTRAIT:
        return ()
    return (Button(Crown.PORTRAIT.command, CROWN_ICON,
                   "Crowned — this Funestra keeps most of the secondary monitor" if crowned
                   else "Give this Funestra the crown — it keeps most of the secondary "
                   "monitor, whatever the Main Funestra shows", lit=crowned),)


def _mode_button(command: str, label: str, *, lit: bool, dim: bool) -> Button:
    return Button(command, label, STILL_STARTING_TOOLTIP if dim else MODE_TOOLTIPS[command],
                  width=FIT_THE_WORD, lit=lit, dim=dim)
