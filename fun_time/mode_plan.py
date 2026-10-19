from __future__ import annotations

from dataclasses import dataclass

from funestra.funestra_verbs import SHOW
from funestra.modes import MainMode

# The main slot's two modes.  The axis is in the name because the satellites'
# own "kino" mode is that same string, and unprefixed the two were one name
# inside command_dispatch, which handles both.
MAIN_KINO_MODE = MainMode.KINO
MAIN_GENAU_MODE = MainMode.GENAU
MAIN_MODES: tuple[MainMode, ...] = (MAIN_KINO_MODE, MAIN_GENAU_MODE)

# The mode every session is BUILT in, whatever it opens in: the defaults
# everywhere — flag files, window bands, a fresh BridgeState — are this one's.
STARTUP_MAIN_MODE = MAIN_KINO_MODE


@dataclass(frozen=True)
class ModeSwitchPlan:
    target_mode: MainMode
    is_transition: bool
    # What the Main Funestra is told: which of Kino and Genau has its window.
    show_cmd: str | None
    main_funestra_should_play: bool | None
    log_message: str


def kino_shows(mode: MainMode) -> bool:
    """Return True if the Main Funestra owns the on-screen display (and its interaction)."""
    return mode == MAIN_KINO_MODE


def show_verb(mode: MainMode) -> str:
    """What the Main Funestra is told so that *mode*'s Funestra has its window."""
    return f"{SHOW} {mode}"


def build_mode_switch_plan(
    *,
    current_mode: MainMode,
    target_mode: MainMode,
    omni_paused: bool,
) -> ModeSwitchPlan:
    """Plan a switch between the main slot's modes; refuse any other."""
    for mode in (current_mode, target_mode):
        if mode not in MAIN_MODES:
            raise ValueError(f"Not a main-slot mode: {mode!r}")
    if current_mode == target_mode:
        return ModeSwitchPlan(
            target_mode=target_mode,
            is_transition=False,
            show_cmd=None,
            main_funestra_should_play=None,
            log_message=f"Already in {target_mode} mode",
        )

    return ModeSwitchPlan(
        target_mode=target_mode,
        is_transition=True,
        show_cmd=show_verb(target_mode),
        main_funestra_should_play=None if omni_paused else kino_shows(target_mode),
        log_message=f"Switched to {target_mode} mode",
    )
