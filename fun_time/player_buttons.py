"""The controls the main console and each satellite's HUD both carry, declared
once so a control reads and looks the same whichever player wears it."""
from __future__ import annotations

from player_core.hud_button import Button
from player_core.hud_marks import MINIMIZE_ICON, shared_mark
from player_core.hud_status import LATEST_LABEL, SHUFFLE_LABEL

PREV_FACE = "⏮"
NEXT_FACE = "⏭"
LOCK_FACE = "🔒"
TRASH_ICON = shared_mark("trash")
RESET_ICON = shared_mark("reset")
SHUFFLE_ICON = shared_mark("shuffle")
LATEST_ICON = shared_mark("latest")
VERSIONS_ICON = shared_mark("versions")
_NONE_FOR_THIS_ONE = " (none for this one)"
MAIN_PLAYER_NOUN = "video"
SATELLITE_NOUN = "clip"


def too_short_to_step(noun: str) -> str:
    return f"this {noun} is 10s or shorter"


def transport(player: str, *, noun: str, longer_than_a_step: bool) -> tuple[Button, ...]:
    too_short = "" if longer_than_a_step else f" ({too_short_to_step(noun)})"
    return (
        Button(f"{player}_prev", PREV_FACE, f"Previous {noun}"),
        Button(f"{player}_nudge_prev", "⏪", f"Back 10s{too_short}",
               dim=not longer_than_a_step),
        Button(f"{player}_nudge_next", "⏩", f"Forward 10s{too_short}",
               dim=not longer_than_a_step),
        Button(f"{player}_next", NEXT_FACE, f"Next {noun}"),
    )


def lock_button(player: str, *, locked: bool, noun: str) -> Button:
    return Button(
        f"{player}_lock", LOCK_FACE,
        f"Locked — this {noun} repeats; press to play on through the playlist" if locked
        else f"Unlocked — plays on through the playlist; press to hold this {noun}",
        lit=locked, favorite=True, group_break=True)


def reset_button(player: str, *, nothing_to_reset: bool) -> Button:
    return Button(
        f"{player}_reset", RESET_ICON,
        "Reset — no filter, no lock, no loop, no F-Mode, normal speed, shuffled from the top",
        dim=nothing_to_reset, group_break=True)


def lit_or_remembered(on: bool, remembered: bool) -> dict:
    return {"lit": on and not remembered, "remembered": on and remembered}


def browse_order_buttons(player: str, *, latest: bool | None,
                         remembered: bool = False) -> tuple[Button, ...]:
    if latest is None:
        return ()
    return (
        Button(f"{player}_shuffle", SHUFFLE_ICON, f"{SHUFFLE_LABEL} — reshuffle what plays",
               group_break=True, **lit_or_remembered(not latest, remembered)),
        Button(f"{player}_latest", LATEST_ICON, f"{LATEST_LABEL} — reload it newest-first",
               **lit_or_remembered(latest, remembered)),
    )


def versions_button(command: str, *, noun: str, has_other_versions: bool) -> Button:
    return Button(
        command, VERSIONS_ICON,
        f"Another version of this {noun}" + ("" if has_other_versions else _NONE_FOR_THIS_ONE),
        dim=not has_other_versions, group_break=True)


def minimize_button(player: str) -> Button:
    return Button(f"{player}_minimize", MINIMIZE_ICON,
                  "Minimize this player — bring it back from the taskbar", group_break=True)
