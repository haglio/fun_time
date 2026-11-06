from __future__ import annotations

from dataclasses import dataclass, fields

from funestra.hud_button import FIT_THE_WORD, Button
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
from funestra.modes import SatellitesMode

from .crown import CROWN_ICON, Crown
from .funestra_buttons import NOUN, minimize_funestra_button
from .origenerator_switches import ORIGENERATOR_MODE, mode_command

MODE_BUTTONS = (
    (SatellitesMode.KINO, "Kino"),
    (SatellitesMode.ORIGENERATOR, "Origenerator"),
)
STILL_STARTING_TOOLTIP = ("Origenerator is still starting — this button becomes enabled "
                          "when it's ready")


@dataclass(frozen=True)
class HudState:
    locked: bool = False
    favorites_filter: bool = False
    latest: bool | None = None
    mode: SatellitesMode | None = None
    origenerator_ready: bool = True
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
    rows: list[tuple[Button, ...]] = []
    if state.mode is not None:
        rows.append(mode_row(funestra, mode=state.mode,
                             origenerator_ready=state.origenerator_ready,
                             in_vr=state.in_vr, crowned=state.crowned))
    rows.append((
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
        *(() if state.in_vr or state.mode is not None
          else (minimize_funestra_button(funestra), *_crown(funestra, state.crowned))),
    ))
    return tuple(rows)


def mode_row(funestra: str, *, mode: SatellitesMode,
             origenerator_ready: bool = True, in_vr: bool = False,
             crowned: bool = False) -> tuple[Button, ...]:
    return (
        *(_mode_button(funestra, button_mode, label, lit=mode is button_mode,
                       dim=button_mode is ORIGENERATOR_MODE and not origenerator_ready)
          for button_mode, label in MODE_BUTTONS),
        *(() if in_vr else (minimize_funestra_button(funestra), *_crown(funestra, crowned))),
    )


def _crown(funestra: str, crowned: bool) -> tuple[Button, ...]:
    if funestra != Crown.PORTRAIT:
        return ()
    return (Button(Crown.PORTRAIT.command, CROWN_ICON,
                   "Crowned — this Funestra keeps most of the secondary monitor" if crowned
                   else "Give this Funestra the crown — it keeps most of the secondary "
                   "monitor, whatever the Main Funestra shows", lit=crowned),)


def _mode_button(funestra: str, mode: SatellitesMode, label: str, *,
                 lit: bool, dim: bool) -> Button:
    return Button(mode_command(funestra, mode), label,
                  STILL_STARTING_TOOLTIP if dim else "",
                  width=FIT_THE_WORD, lit=lit, dim=dim)
