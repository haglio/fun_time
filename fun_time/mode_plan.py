from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from funestra.funestra_verbs import SHOW
from funestra.modes import MainMode

MAIN_KINO_MODE = MainMode.KINO
MAIN_GENAU_MODE = MainMode.GENAU
MAIN_MODES: tuple[MainMode, ...] = (MAIN_KINO_MODE, MAIN_GENAU_MODE)

STARTUP_MAIN_MODE = MAIN_KINO_MODE


@dataclass(frozen=True)
class ModeSwitchPlan:
    target_mode: MainMode
    is_transition: bool
    show_cmd: str | None
    main_funestra_should_play: bool | None
    log_message: str


def kino_shows(mode: MainMode) -> bool:
    return mode == MAIN_KINO_MODE


def show_verb(mode: MainMode) -> str:
    return f"{SHOW} {mode}"


def build_mode_switch_plan(
    *,
    current_mode: MainMode,
    target_mode: MainMode,
    omni_paused: bool,
) -> ModeSwitchPlan:
    for mode in (current_mode, target_mode):
        if mode not in MAIN_MODES:
            raise ValueError(f"Not a Main Funestra mode: {mode!r}")
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


class EnhancementLevel(StrEnum):
    ENHANCED = "enhanced"
    ORIGINAL = "original"
    BOTH = "both"

    @property
    def flash(self) -> str:
        return {EnhancementLevel.ENHANCED: "Enhanced only",
                EnhancementLevel.ORIGINAL: "Original only",
                EnhancementLevel.BOTH: "Enhanced + original"}[self]

    def keep(self, paths: list[str], has_an_enhanced_version: Callable[[str], bool]) -> list[str]:
        if self is EnhancementLevel.BOTH:
            return list(paths)
        wanted = self is EnhancementLevel.ENHANCED
        return [path for path in paths if has_an_enhanced_version(path) == wanted]
