from __future__ import annotations

from dataclasses import dataclass, fields

from funestra.hud_button import Button
from funestra.hud_controls import (
    RESET_TOOLTIP,
    browse_order_buttons,
    favorites_only_tooltip,
    fmode_button,
    funscript_jump_button,
    lock_button,
    lock_tooltip,
    reset_button,
    transport,
    trash_button,
    versions_button,
)

from .crown import CROWN_ICON, Crown
from .funestra_buttons import NOUN, minimize_funestra_button


@dataclass(frozen=True)
class HudState:
    locked: bool = False
    favorites_filter: bool = False
    latest: bool | None = None
    nothing_to_reset: bool = False
    has_other_versions: bool = False
    longer_than_a_step: bool = False
    in_vr: bool = False
    crowned: bool = False
    has_osr2: bool = False
    has_funscript: bool = False

    @classmethod
    def of(cls, hud) -> HudState:
        return cls(**{field.name: getattr(hud, field.name) for field in fields(cls)})


def funestra_rows(funestra: str, state: HudState | None = None) -> tuple[tuple[Button, ...], ...]:
    state = state or HudState()
    return ((
        *transport(funestra, noun=NOUN, longer_than_a_step=state.longer_than_a_step),
        funscript_jump_button(f"{funestra}_funscript_jump", noun=NOUN,
                              has_osr2=state.has_osr2, has_funscript=state.has_funscript),
        lock_button(funestra, locked=state.locked, tooltip=lock_tooltip(state.locked, NOUN)),
        trash_button(funestra),
        fmode_button(funestra, favorites_only_tooltip(), lit=state.favorites_filter),
        reset_button(funestra, nothing_to_reset=state.nothing_to_reset, tooltip=RESET_TOOLTIP),
        *browse_order_buttons(funestra, latest=state.latest),
        versions_button(f"{funestra}_cycle_version", noun=NOUN,
                        has_other_versions=state.has_other_versions),
        *window_buttons(funestra, in_vr=state.in_vr, crowned=state.crowned),
    ),)


def window_buttons(funestra: str, *, in_vr: bool, crowned: bool) -> tuple[Button, ...]:
    return () if in_vr else (minimize_funestra_button(funestra), *_crown(funestra, crowned))


def _crown(funestra: str, crowned: bool) -> tuple[Button, ...]:
    if funestra != Crown.PORTRAIT:
        return ()
    return (Button(Crown.PORTRAIT.command, CROWN_ICON,
                   "Crowned — this Funestra keeps most of the secondary monitor" if crowned
                   else "Give this Funestra the crown — it keeps most of the secondary "
                   "monitor, whatever the Main Funestra shows", lit=crowned),)
