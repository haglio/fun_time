from __future__ import annotations

from funestra.hud_button import Button

from .crown import CROWN_ICON, Crown
from .funestra_buttons import minimize_funestra_button


def window_buttons(funestra: str, *, in_vr: bool, crowned: bool) -> tuple[Button, ...]:
    return () if in_vr else (minimize_funestra_button(funestra), *_crown(funestra, crowned))


def _crown(funestra: str, crowned: bool) -> tuple[Button, ...]:
    if funestra != Crown.PORTRAIT:
        return ()
    return (Button(Crown.PORTRAIT.command, CROWN_ICON,
                   "Crowned — this Funestra keeps most of the secondary monitor" if crowned
                   else "Give this Funestra the crown — it keeps most of the secondary "
                   "monitor, whatever the Main Funestra shows", lit=crowned),)
